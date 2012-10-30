import logging
from config import CHATROOM_PRESENCE
from errbot import BotPlugin
from errbot import botcmd
from twilio import twiml
from twilio.rest import TwilioRestClient
from errbot.builtins.webserver import webhook, OK
from errbot.utils import get_jid_from_message

class PhoneValet(BotPlugin):
    def __init__(self):
        super(PhoneValet, self).__init__()
        self.next_action = {}
        self.pending_calls = {}

    def get_configuration_template(self):
        return {'ERR_SERVER_BASE_URL': 'http://server.domain.tld:3141',
                'ACCOUNT_SID': 'AC00112233445566778899aabbccddeeff',
                'AUTH_TOKEN': '0011223344556677889900aabbccddeeff'}

    def activate(self):
        super(PhoneValet, self).activate()
        self.client = TwilioRestClient(self.config['ACCOUNT_SID'], self.config['AUTH_TOKEN'])


    @botcmd(split_args_with=' ')
    def say_to(self, mess, args):
        """ Call to the specified contact, read you message and record the response
         ex: !say to gbin Don't forget the milk !
        """
        contact = args[0]
        message = ' '.join(args[1:])
        number, _ = self['contacts'][contact]
        from_contact = self.get_current_contact(mess)
        _, from_twilio = self['contacts'][from_contact]

        twilio_response = twiml.Response()
        twilio_response.say(message)
        self.next_action[contact] = (contact + 'has picked up', twilio_response)
        base_url = self.config['ERR_SERVER_BASE_URL']
        self.client.calls.create(to=number,
                                 from_=from_twilio,
                                 url=base_url + '/next_action/%s/' % contact,
                                 status_callback=base_url + '/call_hangup/%s/' % contact,
                                 if_machine='Continue')

        return "Valet: I am calling %s (%s)..." % (contact, number)

    @webhook(r'/next_action/<string:contact>/')
    def act_next_action(self, incoming_request, contact=None):
        if incoming_request['CallStatus'] == 'completed':
            self.send(CHATROOM_PRESENCE[0], 'Valet: %s hanged up' % contact, message_type='groupchat')
            return OK
        logging.debug("Next action for contact=" + contact)
        action_name, response = self.next_action[contact]
        self.send(CHATROOM_PRESENCE[0], 'Valet: %s...' % action_name, message_type='groupchat')
        return response.toxml()

    @webhook(r'/call_hangup/<string:contact>/')
    def call_hangup(self, incoming_request, contact=None):
        self.send(CHATROOM_PRESENCE[0], 'Valet: %s\'s call has ended.' % contact, message_type='groupchat')

    @webhook(r'/incoming_call/')
    def incoming_call(self, incoming_request):
        logging.info("Incoming call to %s from %s" % (incoming_request['Called'], incoming_request['Caller']))
        twilio_response = twiml.Response()
        for name, (real, twilio) in self.get('contacts', {}).iteritems():
            if twilio == incoming_request['Called']:
                self.send(CHATROOM_PRESENCE[0],
                          '@%s %s is calling you... what do you want to do ?\n\n    !msg : answer it with a message\n    !vm: redirect him to voicemail\n    !fw NAME: redirect it to this other person' % (
                              name, incoming_request['Caller']), message_type='groupchat')
                twilio_response.addSay("Please wait a moment while we try to contact %s" % name)
                twilio_response.addPause(length=10)
                twilio_response.addDial(real)
                self.pending_calls[name] = dict(incoming_request)
                return twilio_response.toxml()

    @webhook(r'/incoming_sms/')
    def incoming_call(self, incoming_request):
        logging.info("Incoming sms to %s from %s" % (incoming_request['To'], incoming_request['From']))
        for name, (real, twilio) in self.get('contacts', {}).iteritems():
            if twilio == incoming_request['To']:
                self.send(CHATROOM_PRESENCE[0],
                          '@%s %s is sending you an SMS :\n\n "%s"' % (
                              name, incoming_request['From'], incoming_request['Body']), message_type='groupchat')
        return OK


    @webhook(r'/incoming_vm/<string:contact>/')
    def incoming_vm(self, incoming_request, contact=None):
        logging.info("Incoming vm to %s from %s" % (contact, incoming_request['From']))
        self.send(CHATROOM_PRESENCE[0], '@%s %s has left a message, here is what I understood from it :\n\n    "%s"\n\n    Click here to listen to the audio message: %s ' % (
            contact, incoming_request['From'], incoming_request['TranscriptionText'], incoming_request['RecordingUrl']), message_type='groupchat')
        return OK

    def set_next_action(self, contact, feedback, twilio_response):
        base_url = self.config['ERR_SERVER_BASE_URL']
        call = self.pending_calls[contact]
        self.next_action[contact] = (contact + feedback, twilio_response)
        self.client.calls.route(call['CallSid'][0], base_url + '/next_action/%s/' % contact)

    def answers_record_transcribe(self, contact, message="Sorry. I am not available. Please leave a message.", feedback=' your caller is beeing asked to leave a message...'):
        logging.info('contact = %s' % contact)

        twilio_response = twiml.Response()
        twilio_response.addSay(message)
        twilio_response.addRecord(transcribeCallback=self.config['ERR_SERVER_BASE_URL'] + '/incoming_vm/%s/' % contact)
        self.set_next_action(contact, feedback, twilio_response)
        return "Valet: Notifying your caller."

    def get_current_contact(self, mess):
        return get_jid_from_message(mess).split('@')[0]

    @botcmd
    def vm(self, mess, args):
        """ Answer your incoming call and just play a standard "Sorry. I am not available. Please leave a message." and record the answer from the caller.
         usage: !vm
        """
        return self.answers_record_transcribe(self.get_current_contact(mess))

    @botcmd
    def msg(self, mess, args):
        """ Answer your incoming call and the play a custom message passed as argument then record the response.
         usage: !msg I am busy at the office, please call back in one hour.
        """
        return self.answers_record_transcribe(self.get_current_contact(mess), args, feedback=' your caller is listening to your message')

    @botcmd
    def fw(self, mess, args):
        """ Forward your incoming call to another contact registered on the system.
         usage: !fw john
        """
        contact = self.get_current_contact(mess)
        real, twilio = self['contacts'][args]
        twilio_response = twiml.Response()
        twilio_response.addDial(real)
        self.set_next_action(contact, contact + " your incoming call is forwarded to %s (%s)" % (args, real), twilio_response)
        return "Valet: trying to forward your call"

    @botcmd(split_args_with=' ')
    def sms_to(self, mess, args):
        """ Sends an sms to the specified contact.
        usage: !sms to john Hey, how are you doing ?
         """
        contact = args[0]
        message = ' '.join(args[1:])
        number, _ = self['contacts'][contact]
        from_contact = self.get_current_contact(mess)
        _, from_twilio = self['contacts'][from_contact]
        self.client.sms.messages.create(to=number,
                                        from_=from_twilio, body=message)

        return "Valet: I sent the message to %s (%s)..." % (contact, number)


    @botcmd(split_args_with=' ')
    def add_contact(self, mess, args):
        """Add a contact to the contact list
        !add contact NAME REAL_NUMBER [TWILIO_NUMBER]
        """
        contacts = self.get('contacts', {})
        if len(args) == 3:
            contacts[args[0]] = (args[1], args[2])
        elif len(args) == 2:
            contacts[args[0]] = (args[1], None)
        else:
            return "The syntax is !add contact NAME REAL_NUMBER [TWILIO_NUMBER]"
        self['contacts'] = contacts
        return 'Contact %s added' % args[0]

    @botcmd(split_args_with=' ')
    def del_contact(self, mess, args):
        """Remove a contact
         ex : !del contact gbin
        """
        contacts = self.get('contacts', {})
        del contacts[args[0]]
        self['contacts'] = contacts
        return 'Contact %s deleted' % args[0]

    @botcmd(split_args_with=' ')
    def contacts(self, mess, args):
        """Lists the current registered contacts.
        ex : !contacts
        """
        return '\n'.join("%s -> re:%s tw:%s" % (entry, tw, re) for entry, (tw, re) in self.get('contacts', {}).iteritems())
