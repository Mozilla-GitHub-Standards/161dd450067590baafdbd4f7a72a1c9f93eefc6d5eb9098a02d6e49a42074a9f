import logging

import urllib, urllib2, simplejson
import email, smtplib
import sys
import time, datetime
import tzone

SMTP_SENDER_DEFAULT='cltbld@build.mozilla.com'
SMTP_SERVER_DEFAULT='localhost'

WT_SERVICE_DEFAULT='http://localhost:5000/waittimes'
WT_POOL_DEFAULT='buildpool'
WT_MPB_DEFAULT=15 			# minutes_per_block
WT_RECEIVERS_DEFAULT=[]
WT_STARTTIME_DEFAULT=None
WT_ENDTIME_DEFAULT=None

log = logging.getLogger('buildapi.controllers.mailwaittimes')

def format_wait_times(wait_times):
    """Format wait times statistics in a pretty human readable format.

    The result is a tuple, first element being a short summary (e.g. subject for e-mails),
    the second is the actual text (e.g. message body).

    Input: wait_times - wait times object
    Output: (title, text) - sting formatted wait times
    """
    total = wait_times['total']
    minutes_per_block = wait_times['minutes_per_block']

    zero_wait = wait_times['wt'].get('0',{'total':0})['total']*100./total if total > 0 else 0
    title = "Wait: %i/%.2f%% (%s)" % (total, zero_wait, wait_times['pool'])

    text = []
    text.append("Wait time report for %s for jobs submitted between %s and %s\n" 
                % (wait_times['pool'], tzone.pacific_time(wait_times['starttime']), tzone.pacific_time(wait_times['endtime'])))
    text.append("Total Jobs: %s\n" % total)
    text.append("Wait Times")
    text.append(format_wait_times_stats(wait_times['wt'], minutes_per_block, total))
    
    text.append("\nPlatform break down\n")
    for platform in sorted(wait_times['platforms']):
        pwt = wait_times['platforms'][platform]
        text.append("%s: %s" % (platform, pwt['total']))
        text.append(format_wait_times_stats(pwt['wt'], minutes_per_block, pwt['total']))
        text.append("\n")

    text.append("The number on the left is how many minutes a build waited to start, rounded down.\n")
    text.append("Builds with no changes (usually nightly builds): %s.\n" % wait_times['no_changes'])
    text.append("Rebuilds and forced rebuilds were excluded from the statistics.\n\n")
    if wait_times['otherplatforms']:
        text.append("Other platforms lister under 'other' platforms: %s.\n" 
            % ', '.join(wait_times['otherplatforms']))
    if wait_times['unknownbuilders']:
        text.append("Unknown builders (excluded from the stats): %s.\n\n" % ', '.join(wait_times['unknownbuilders']))
    text.append("Generated at %s. All times are Mountain View, CA (US/Pacific)." % tzone.pacific_time(None))

    return (title, '\n'.join(text))

def format_wait_times_stats(stats, minutes_per_block, total=0):
    """Format wait time statistics for one platform only.
    
    Input: stats - wait times for one platform, {'block_no': num,}
           minutes_per_block - granularity of wait times blocks
           total - number of all build requests for this platform
    Ouput: string - formatted text
    """
    if not total: total = sum(stats.values())

    text = []
    max_block = max(map(int, stats.keys()))+1 if stats.keys() else 0
    for i in range(0, max_block, minutes_per_block):
        num = stats.get(str(i), {'total': 0})['total']
        percentage = " %8.2f%%" % (num*100./total) if total > 0 else ''
        
        text.append("%3i: %8i%s" % (i, num, percentage))

    return "\n".join(text)

def wtservice_get_full_url(wt_service=WT_SERVICE_DEFAULT, 
        pool='buildpool', starttime=None, endtime=None, minutes_per_block=None):
    """Returns the full request URL to the service providing the wait times.
    
    Input: wt_service - base URL for service, default: http://localhost:5000/waittimes
           pool - pool name, default: buildpool
           starttime - start time, default: None (server's default will be used)
           endtime - end time, default: None (server's default will be used)
           minutes_per_block - block granularity, default: None (server's default will be used)
    Output: string - full request URL
    """
    wt_params = dict(format='json', startime=starttime, endtime=endtime, mpb=minutes_per_block)
    wt_params_str = urllib.urlencode([(k, v) for k, v in wt_params.items() if v])

    return '%s/%s?%s' % (wt_service, pool, wt_params_str)

def wtservice_fetch(url):
    """Makes a HTTP request to the URL provided and fetches the wait times.
    
    Input: url - request URL
    Output: wait times object (dictionary)
    Throws: urllib2.URLError - opening connection fails
            ValueError - decoding JSON object response fails
    """
    resp = urllib2.urlopen(url)
    text = resp.read()
    wait_times = simplejson.loads(text)

    return wait_times['waittimes']
	
def mail_send(body, subject, sender, receivers, server):
    """Sends an e-mail. This method will return normally if the mail is accepted 
    for at least one recipient. 
    
    Input: body - message
           subject - subject
           sender - sender's e-mail address
           receivers - list of receiver e-mails
           server - SMTP server
    Output: dictionary, with one entry for each recipient that was refused
    Raised Errors: any error raised by stmplib.STMP and stmp.sendmail. e.g. 
           SMTPConnectError, SMTPRecipientsRefused, SMTPSenderRefused, etc.
           (see stmplib documentation)
    """
    headers = []
    headers.append("Subject: %s" % subject)
    headers.append("From: %s" % sender)
    headers.append("To: %s" % (", ".join(receivers)))
    headers.append("Date: %s" % email.utils.formatdate(localtime=True))    
    message = "\n".join(headers) + "\n" + body
    
    smtp = smtplib.SMTP(server)
    refused_rcv = smtp.sendmail(sender, receivers, message)
    smtp.quit()

    return refused_rcv
	
def mail_wait_times(server=SMTP_SERVER_DEFAULT, sender=SMTP_SENDER_DEFAULT, receivers=WT_RECEIVERS_DEFAULT, 
        wt_service=WT_SERVICE_DEFAULT, pool=WT_POOL_DEFAULT, starttime=WT_STARTTIME_DEFAULT, 
        endtime=WT_ENDTIME_DEFAULT, minutes_per_block=WT_MPB_DEFAULT):
    """Mails wait times. Main function to call for mailing wait times.
    
    Input: server - SMTP server
           sender - sender's e-mail address
           receivers - list of receiver e-mails
           wt_service - wait times service base URL, e.g. http://domain/waittimes
           pool - pool name
           minutes_per_block - minutes per block
           starttime - start time in seconds since epoch, UTC (end time minus 24 hours, if not specified)
           endtime - end time in seconds since epoch, UTC (start time plus 24 hours, if not specified)
    Output: Dictionary containing the status of the operation: success or error, and additional information. 
           In case of success, it looks like: {status: 'success', refused: refused_rcv}
           , where refused_rcv is a dictionary, with one entry for each recipient that was refused, thus 
           empty if all recipients received the email.
           In case of error/failure, the result looks like: {status: 'error', msg: 'reason'}, if all of 
           the receivers were refused or an exception was raised.
    """
    wt_full_url = wtservice_get_full_url(wt_service=wt_service, pool=pool, starttime=starttime, 
        endtime=endtime, minutes_per_block=minutes_per_block)

    err_msg = ''
    try:
        wait_times = wtservice_fetch(wt_full_url)
        subject, body = format_wait_times(wait_times)
        refused_rcv = mail_send(body, subject, sender, receivers, server)
        
        msg = 'E-mail (subject: %s) send successfully to at least one receiver from: %s \
        (refused recipients: %s)' % (subject, ', '.join(receivers), refused_rcv)

        return {'status': 'success', 'refused': refused_rcv, 'msg': msg}
    except urllib2.URLError, e:
        err_msg = 'Error: fetching wait times from location %s: %s' % (wt_full_url, e)
    except Exception, e:
        err_msg = 'Error: unable to send email (Cause: %s)' % e
    
    return {'status': 'error', 'msg': err_msg}

if __name__ == '__main__':
    from optparse import OptionParser

    parser = OptionParser()
    parser.set_defaults(
        smtp_server = SMTP_SERVER_DEFAULT,
        sender = SMTP_SENDER_DEFAULT,
        receivers = WT_RECEIVERS_DEFAULT,
        wt_service = WT_SERVICE_DEFAULT,
        pool = WT_POOL_DEFAULT,
        minutes_per_block = WT_MPB_DEFAULT,
        starttime = WT_STARTTIME_DEFAULT,
        endtime = WT_ENDTIME_DEFAULT,
    )

    parser.add_option("-S", "--smtp", dest="smtp_server", help="SMTP server")    
    parser.add_option("-f", "--from", dest="sender", help="Sender's e-mail address")
    parser.add_option("-a", "--address", dest="receivers", action="append", help="Receiver e-mail address")
    parser.add_option("-W", "--wt", dest="wt_service", help="Wait times service base URL, e.g. http://domain/waittimes")
    parser.add_option("-p", "--pool", dest="pool", help="Pool name")
    parser.add_option("-m", "--minutes-per-block", type="int", help="How many minutes per block", dest="minutes_per_block")
    parser.add_option("-s", "--start-time", dest="starttime", type="int", 
        help="Start time in seconds since epoch, UTC. If not specified, it will equal end time minus 24 hours")
    parser.add_option("-e", "--end-time", dest="endtime", type="int", 
        help="End time in seconds since epoch, UTC. If not specified, it will equal start time plus 24 hours")

    op, args = parser.parse_args()

    resp = mail_wait_times(server=op.smtp_server, sender=op.sender, receivers=op.receivers, 
        wt_service=op.wt_service, pool=op.pool, starttime=op.starttime, endtime=op.endtime, 
        minutes_per_block=op.minutes_per_block)

    print resp['msg'] 
    if resp['status'] == 'error':
        sys.exit(1)		# an exception was thrown, the mail was not sent to any of the receivers  
    if len(resp['refused'].keys()) > 0:
        sys.exit(2)     # mail was not sent to all receivers (just a subset)
    else: 
        sys.exit(0)	    # success - mail sent to all receivers