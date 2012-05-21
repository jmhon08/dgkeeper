import cgi
import os
import logging
import math
import random
import hashlib
import time
import datetime
import re


from django.utils import simplejson
from google.appengine.ext.webapp import template
from google.appengine.api import users
from google.appengine.ext import webapp
from google.appengine.api import mail
from google.appengine.ext.webapp.util import run_wsgi_app
from google.appengine.ext import db
from Pubnub import Pubnub

PUBLISH_KEY = 'pub-12c24617-d9d5-4ca2-bfa8-32a8b5b51516'
SUBSCRIBE_KEY = 'sub-b9a8c2eb-c6c4-11e0-b0ec-9d319456cf89'
SECRET_KEY = 'xxxxx'
SECURE_CONNECT = True
pubnub = Pubnub(PUBLISH_KEY, SUBSCRIBE_KEY, SECRET_KEY, SECURE_CONNECT)

class States1(db.Model):
    url = db.StringProperty()
    state = db.StringProperty()
    timestamp = db.StringProperty()

class MainPage(webapp.RequestHandler):
    def get(self):
        roomURL = self.request.path
        state = ''
        lastUpdateTime = ''
        urlStateDBMessage = db.GqlQuery("SELECT * FROM States1 WHERE url = :1", roomURL)
        #If room already exists, then pass state to client
        if (urlStateDBMessage.count()!=0):
            for message in urlStateDBMessage:
                state = message.state
                lastUpdateTime = message.timestamp
                logging.info('pass existing room state to client')

        template_values = {
            'state' : state,
            'roomURL' : roomURL,
            'lastUpdateTime' : lastUpdateTime,
            }
        path = os.path.join(os.path.dirname(__file__), 'joyce.html')
        self.response.out.write(template.render(path, template_values))    

class testPage(webapp.RequestHandler):
    def get(self):   
        template_values = {}
        path = os.path.join(os.path.dirname(__file__), 'test.html')
        self.response.out.write(template.render(path, template_values))          

class storeState(webapp.RequestHandler):
    def post(self):
        logging.info('storing state to server')
        #get latest state from client
        state = self.request.get('state','')
        roomURL = self.request.get('url','')
        timestamp = self.request.get('timestamp','')
        changeType = self.request.get('type','')
        
        ####update the db
        #if url exists, update the state for the url
        urlStateDBMessage = db.GqlQuery("SELECT * FROM States1 WHERE url = :1", roomURL)
        if (urlStateDBMessage.count()!=0):
            for message in urlStateDBMessage:
                #only update if the incoming update has a timestamp later than the last update
                if (message.timestamp < timestamp):
                    message.state = state
                    message.timestamp = timestamp
                    message.put()
                    logging.info('updated state')
                    
                #if player got added, then force subscription
                if (changeType == 'newPlayer'):
                    logging.info('pubnub for new player')
                    roomURLNewPlayer = roomURL+'-new'
                    
                    pubnub.publish({
                        'channel' : roomURLNewPlayer,
                        'message' : {
                            'state' : state,
                            'timestamp' : timestamp,
                        }
                    })
                #if new hole got added, then force subscription
                #if not a new hole or new player, then it's a new game, so just store and don't publish anything
                elif (changeType=='nextHole'):
                    logging.info('pubnub for new hole')
                    roomURLNextHole = roomURL+'-next'
                    pubnub.publish({
                        'channel' : roomURLNextHole,
                        'message' : {
                            'state' : state,
                            'timestamp' : timestamp,
                        }
                    })
                    
        else:
            #else, make a new row 
            newState = States1()
            newState.url = roomURL
            newState.state = state
            newState.timestamp = timestamp
            newState.put()
            logging.info('created new state')

class adjustState(webapp.RequestHandler):
    def post(self):
        logging.info('adjusting state in server')
        #get things to adjust from client
        updateIndex = int(self.request.get('updateIndex',''))
        updateHole = int(self.request.get('updateHole',''))
        updateHoleIndex = updateHole-1
        updateScore = int(self.request.get('updateScore',''))
        timestamp = self.request.get('timestamp','')
        roomURL = self.request.get('url','')
        
        ####update the db
        #If url exists, update the state for the url only if incoming timestamp is later than the one stored
        urlStateDBMessage = db.GqlQuery("SELECT * FROM States1 WHERE url = :1", roomURL).get()
        if (urlStateDBMessage):
            state=simplejson.loads(urlStateDBMessage.state)
            if (urlStateDBMessage.timestamp < timestamp):
                #get the scores array to be changed and update the score
                updateScoreArray = state[str(updateIndex)]['scores']
                logging.info(updateHoleIndex)
                logging.info(updateScoreArray)
                logging.info(updateScoreArray[updateHoleIndex])
                updateScoreArray[updateHoleIndex] = updateScore

                #update state with the new array
                state[str(updateIndex)]['scores'] = updateScoreArray
        
                urlStateDBMessage.state = simplejson.dumps(state)
                urlStateDBMessage.timestamp = timestamp
                urlStateDBMessage.put()
            
                #Publish to client side
                logging.info('pubnub')
                pubnub.publish({
                'channel' : roomURL,
                    'message' : {
                        'playerIndex': updateIndex,
                        'hole': updateHole,
                        'score' : updateScore,
                        'timestamp' : timestamp,
                    }
                })
            else:
                logging.info('this is an older record')
                self.response.out.write('older record received first')
                                            
        else:
            logging.info('this row does not exist')
          
          
class syncState(webapp.RequestHandler):
    def post(self):
        logging.info('syncing state with server')
        #get latest state from client
        clientStateMD5 = self.request.get('stateMD5','')
        roomURL = self.request.get('url','')
        timestamp = self.request.get('timestamp', '')

        ####update the db
        #if url exists, compare md5 of server state and client state
        urlStateDBMessage = db.GqlQuery("SELECT * FROM States1 WHERE url = :1", roomURL).get()
        if (urlStateDBMessage):
            storedState=simplejson.loads(urlStateDBMessage.state)
            stringedStoredState = simplejson.dumps(sorted([[k,storedState[k]] for k in storedState]))
            
            storedStateMD5 = hashlib.md5(re.sub(r'[^\w]','',stringedStoredState)).hexdigest()
            
            #if md5's are different, then update client with the server state
            if (storedStateMD5 != clientStateMD5):
                logging.info('Stored state is different from client state. Sending saved state to client...')
                logging.info(storedState)
                self.response.out.write(urlStateDBMessage.state)
      

class Emailer(webapp.RequestHandler):
    def post(self):
        #logging.info(self.request.get('jsonn',''))
        items = simplejson.loads(self.request.get('jsonn',''))
        scoredump = ''
        for index, item in enumerate(items):
            if index == (len(items)-1):
                break
            for j in item:
                scoredump += '%s\t' % j

            if index == 0:
                scoredump += 'Players \n'
            elif index == (len(items)-2):
                scoredump += 'Total\n'
            else:
                scoredump += 'Hole %d\n' % index
        logging.info(scoredump)
        mail.send_mail(sender="DGKeeper <jmhon08@gmail.com>",
                      to="%s" % items[len(items)-1][0],
                      subject="DGKeeper scores for " + str(datetime.date.today()),
                      body="""
%s
""" % scoredump)

# class generateURL(webapp.RequestHandler):
#     def get(self):
#         logging.info ('make new url')
#         unique="N"

#         def createURL():
#                 url = ""
#                 possible = "abcdefghijklmnopqrstuvwxyz01234567899";
#                 numOfDigits = 4

#                 for i in range(0,numOfDigits):
#                     position = int(math.floor(random.random()*36))
#                     url += possible[position]
#                 return url

#         while(unique=="N"):
#             url = '/'+createURL()
#             findURL = db.GqlQuery("SELECT * FROM States1 WHERE url = :1", url)
#             if (findURL.count()==0):
#                 unique="Y"
#                 self.redirect(url)

application = webapp.WSGIApplication([
                                ('/email-scores',Emailer),
                                ('/store', storeState),
                                ('/sync', syncState),
                                ('/adjust', adjustState),
                                ('/test', testPage),
                                ('/.*', MainPage),
                              ],
                             debug=True)

def main():
    run_wsgi_app(application)

if __name__ == "__main__":
    main()
