import asyncio
import datetime
import logging
import hmac
from base64 import b64encode
from time import time

from twilio.rest import Client as TwilioClient
from twilio.base.exceptions import TwilioException, TwilioRestException

from camus import app, db
from camus.models import Client, Room


class LoopTimer:
    def __init__(self, timeout, callback, **kwargs):
        self._timeout = timeout
        self._callback = callback
        self._task = asyncio.create_task(self._run())
        self._kwargs = kwargs

    async def _run(self):
        while True:
            try:
                await asyncio.sleep(self._timeout)
                await self._callback(**self._kwargs)
            except Exception as e:
                logging.error(e)

    def cancel(self):
        self._task.cancel()


async def ping_clients(message_handler):
    now = datetime.datetime.utcnow()
    clients = Client.query.filter(Client.seen < now - datetime.timedelta(seconds=10)).all()
    logging.info('\t-> Ping clients: {}'.format(clients))

    for client in clients:
        message_handler.send_ping(client.uuid)


async def reap_clients(message_handler):
    now = datetime.datetime.utcnow()
    clients = Client.query.filter(Client.seen < now - datetime.timedelta(seconds=30)).all()
    logging.info('\t-> Reap clients: {}'.format(clients))

    for client in clients:
        message_handler.send_bye(client.uuid)
        db.session.delete(client)
        db.session.commit()


async def reap_rooms():
    now = datetime.datetime.utcnow()
    rooms = Room.query.filter(Room.active < now - datetime.timedelta(seconds=60)).all()
    logging.info('\t-> Reap rooms: {}'.format(rooms))

    for room in rooms:
        db.session.delete(room)
        db.session.commit()

def get_ice_servers(username):
    """Get a list of configured ICE servers."""

    stun_host = app.config['STUN_HOST']
    stun_port = app.config['STUN_PORT']
    stun_url = 'stun:{}:{}'.format(stun_host, stun_port)
    servers = [{'urls': [stun_url]}]

    turn_host = app.config['TURN_HOST']
    turn_port = app.config['TURN_PORT']
    turn_key = app.config['TURN_STATIC_AUTH_SECRET']

    if turn_host and turn_port and turn_key:
        turn_url = 'turn:{}:{}'.format(turn_host, turn_port)
        username, password = generate_turn_creds(turn_key, username)
        servers.append({'urls': [turn_url], 'username': username, 'credential': password})

    servers += get_twilio_ice_servers()

    return servers


def get_twilio_ice_servers():
    """Fetch a list of ICE servers provided by Twilio."""

    account_sid = app.config['TWILIO_ACCOUNT_SID']
    auth_token = app.config['TWILIO_AUTH_TOKEN']
    key_sid = app.config['TWILIO_KEY_SID']

    try:
        twilio = TwilioClient(key_sid, auth_token, account_sid)
        token = twilio.tokens.create()
        return token.ice_servers
    except (TwilioException, TwilioRestException):
        return []


def generate_turn_creds(key, username):
    """Generate TURN server credentials for a client."""

    expiration = int(time()) + 6 * 60 * 60  # creds expire after 6 hrs
    username = '{}:{}'.format(expiration, username)
    token = hmac.new(key.encode(), msg=username.encode(), digestmod='SHA1')
    password = b64encode(token.digest()).decode()

    return username, password
