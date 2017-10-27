from boto3 import resource
from chardet import detect
from slackweb import Slack

from base64 import b64decode
from email import message_from_bytes
from email.header import decode_header, make_header
from os import environ
from yaml import load

import re

class Email:
    def __init__(self, msg):
        self.msg = message_from_bytes(msg)

    def __header_value(self, v):
        return str(make_header(decode_header(self.msg.get(v, failobj=''))))

    def from_(self):
        return self.__header_value('from')

    def to(self):
        return self.__header_value('to')

    def subject(self):
        return self.__header_value('subject')

    def __multipart_body(self):
        body = ''
        for part in self.msg.get_payload():
            if part.get_content_type() == 'text/plain':
                body = part.get_payload(decode=True)
        return body

    def body(self):
        body = self.__multipart_body() if self.msg.is_multipart() else self.msg.get_payload(decode=True)
        if body == '':
            return ''
        charset = self.msg.get_param('charset')
        charset = detect(body)['encoding'] if charset is None else charset
        return str(body, encoding=charset) if charset is not None else ''

class Filter(Email):
    def __is_matched(self, filter_, filter_key, string):
        return re.search(filter_[filter_key], string, re.IGNORECASE) \
            if filter_key in filter_ and filter_[filter_key] is not None else None

    def __matched_filter(self, filters):
        for filter_ in filters:
            if 'post_channel' not in filter_ or filter_['post_channel'] is None:
                continue

            if 'including_words' in filter_ and 'excluded_words' in filter_:
                if self.__is_matched(filter_, 'including_words', self.fulltext) \
                   and not self.__is_matched(filter_, 'excluded_words', self.fulltext):
                    return filter_

            elif 'including_words' in filter_ and 'excluded_words' not in filter_:
                if self.__is_matched(filter_, 'including_words', self.fulltext):
                    return filter_

            elif 'including_words' not in filter_ and 'excluded_words' in filter_:
                if self.__is_matched(filter_, 'excluded_words', self.fulltext):
                    return filter_

            if self.__is_matched(filter_, 'from', self.from_) \
               or self.__is_matched(filter_, 'to', self.to) \
               or self.__is_matched(filter_, 'subject', self.subject):
                return filter_

        return filters[-1]


    def __init__(self, email, filters):
        self.from_ = email.from_()
        self.to = email.to()
        self.subject = email.subject()
        self.body = email.body()
        self.fulltext = str(self.from_ + self.to + self.subject + self.body).replace('\n', '').replace('\r', '').replace(' ', '')
        self.matched_filter_ = self.__matched_filter(filters)

    def is_matched(self):
        return False if self.matched_filter_ is None else True

    def matched_filter(self):
        return self.matched_filter_

def lambda_handler(event, context):
    config = load(b64decode(environ.get('config', '[]')))
    if config is None:
        print('')
        exit(1)

    if 'webhook_url' not in config:
        print('webhook_url is required.')
        exit(1)

    if 'filter' not in config:
        config = {'filter': []}

    username = config['username'] if 'username' in config else 'e2s'

    s3 = resource('s3')
    bucket = s3.Bucket(event['Records'][0]['s3']['bucket']['name'])
    obj = bucket.Object(event['Records'][0]['s3']['object']['key'])
    resp = obj.get()

    channel = '#general'
    color = 'good'

    email = Email(resp['Body'].read())

    filter_ = Filter(email, config['filter'])
    if filter_.is_matched():
        f = filter_.matched_filter()
        channel = f['post_channel']
        color = f['attachments_color']

    print(channel, color)

    slack = Slack(url=config['webhook_url'])
    attachments=[{'color': color, 'pretext': email.from_(), 'title': email.subject(), 'text': email.body()}]
    slack.notify(channel=channel, username=username, attachments=attachments)

if __name__ == '__main__':
    import time

    bucket = environ.get('bucket', None)
    if bucket is None:
        print('debug: config=$(base64 -w0 config.yml) bucket=bucket_name python {}'.format(__file__))
        exit(1)

    limit = int(environ.get('limit', '3'))

    for obj in resource('s3').Bucket(bucket).objects.limit(count=limit):
        event = {
            'Records': [{
                's3': {
                    'bucket': {
                        'name': bucket,
                    },
                    'object': {
                        'key': obj.key,
                    },
                },
            }],
        }
        lambda_handler(event, None)
        time.sleep(1)
