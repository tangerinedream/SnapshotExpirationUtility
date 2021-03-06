#!/usr/bin/python

import boto3
import pytz
import argparse
import logging
import logging.handlers
import time

from time import strftime
from datetime import datetime, timedelta
from dateutil.relativedelta import *
from grandfatherson import dates_to_keep, dates_to_delete, SATURDAY


class SnapClean(object):

    DATE_FORMAT="%a %b %d %Y"

    def __init__(self, region, policyDay, policyWeek, policyMonth, tagKey, tagValue, account, logLevel, dryRunFlag):
        self.region = region
        self.policyDay = policyDay
        self.policyWeek = policyWeek
        self.policyMonth = policyMonth
        self.tagKey = tagKey
        self.tagValue = tagValue
        self.account = account
        self.logLevel = logLevel
        self.dryRunFlag = dryRunFlag
        self.currDateTime = datetime.now(pytz.utc)
        self.initLogging(self.logLevel)

    def initLogging(self, loglevel):
        # Setup the Logger
        self.logger = logging.getLogger('SnapClean')  # The Module Name

        # Set logging level
        loggingLevelSelected = logging.INFO

        if (loglevel == 'critical'):
            loggingLevelSelected = logging.CRITICAL
        elif (loglevel == 'error'):
            loggingLevelSelected = logging.ERROR
        elif (loglevel == 'warning'):
            loggingLevelSelected = logging.WARNING
        elif (loglevel == 'info'):
            loggingLevelSelected = logging.INFO
        elif (loglevel == 'debug'):
            loggingLevelSelected = logging.DEBUG
        elif (loglevel == 'notset'):
            loggingLevelSelected = logging.NOTSET

        filenameVal = self.region + '_' + self.tagValue + '_SnapClean.log'
        log_formatter = logging.Formatter('[%(asctime)s][%(levelname)s]%(message)s')

        # Add the rotating file handler
        handler = logging.handlers.RotatingFileHandler(
            filename=filenameVal,
            mode='a',
            maxBytes=512 * 1024,
            backupCount=10)
        handler.setFormatter(log_formatter)

        self.logger.addHandler(handler)
        self.logger.setLevel(loggingLevelSelected)

    def generateInclusionDatesList(self):

        end_range_date = datetime.now(pytz.utc).date()
        start_range_date = datetime.now(pytz.utc)

        # Calculate the date range where snapshots will be evaluated against by the grandfatherson algorithm.
        if self.policyMonth > 0:
            start_range_date = end_range_date - relativedelta(months=self.policyMonth)
        elif self.policyWeek > 0:
            start_range_date = end_range_date - relativedelta(weeks=self.policyWeek)
        elif self.policyDay > 0:
            start_range_date = end_range_date - relativedelta(days=self.policyDay)
        else:
            self.logger.error('error: no policy values specified, exiting') 
            sys.exit(-1)

        self.logger.info( 'Retention Policy: %(daily)s Daily, %(weekly)s Weekly, and %(monthly)s Monthly' % {
           'daily': self.policyDay,
           'weekly': self.policyWeek,
           'monthly': self.policyMonth
        })
        self.logger.info( 'Start of Range date in UTC: ' + start_range_date.strftime(SnapClean.DATE_FORMAT) )
        self.logger.info( 'End of Range date in UTC: ' + end_range_date.strftime(SnapClean.DATE_FORMAT) )

        inclusionDatesList = [start_range_date + timedelta(days=i) for i in range((end_range_date - start_range_date).days + 1)]

        # This is where the Grandfatherson algorithm is put in play
        return sorted( 
            dates_to_keep( 
                inclusionDatesList,  
                days=self.policyDay,
                weeks=self.policyWeek,
                months=self.policyMonth,
                firstweekday=SATURDAY, 
                now=end_range_date
            )
        )


    def execute(self):

        # Log the duration of the processing
        startTime = datetime.now().replace(microsecond=0)

        self.logger.info('============================================================================')

        # Get the EC2 Service Resource
        ec2ServiceResourceAPI = boto3.resource('ec2', region_name=self.region)
        snapshotAPI = ec2ServiceResourceAPI.snapshots

        # Step 1: Get all snapshots matching the Tag/Value
        targetFilter = [
            {
                'Name': 'status',
                'Values': ['completed']
            },
            {
                'Name': 'owner-id',
                'Values': [self.account]
            },
            {
                'Name': 'tag:' + self.tagKey,
                'Values': [self.tagValue]
            }
        ]

        snapshot_iterator = iter([])  # empty iter for scope purposes
        try:
            snapshot_iterator = snapshotAPI.filter(
                Filters=targetFilter
            )
        except Exception as e:
            self.logger.error('Exception filtering snapshots %s' + str(e))

        # Step #2: Collect all snapshots older than retentionTime
        TOTAL_SNAPSHOTS_FOUND = 'totalSnapshotsFound'
        EXPIRED_SNAPSHOTS_FOUND = 'expiredSnapshotsFound'
        SNAPSHOTS_DELETED = 'deletedSnapshots'
        EXCEPTIONS_ENCOUNTERED = 'exceptionsEncountered'

        results = {TOTAL_SNAPSHOTS_FOUND: 0, EXPIRED_SNAPSHOTS_FOUND: 0, SNAPSHOTS_DELETED: 0, EXCEPTIONS_ENCOUNTERED: 0}

        expiredSnapshots = []

        inclusionDatesList = self.generateInclusionDatesList()

        for item in inclusionDatesList:
            self.logger.info(item.strftime(SnapClean.DATE_FORMAT))

        for snapshot in snapshot_iterator:

            assert isinstance(snapshot.start_time, datetime), '%r is not a datetime' % snapshot.start_time

            results[TOTAL_SNAPSHOTS_FOUND] = results[TOTAL_SNAPSHOTS_FOUND] + 1

            snapshotStartDateTime = snapshot.start_time.date()
            if (snapshotStartDateTime not in inclusionDatesList):
                self.logger.info('Snapshot %s with date %s is NOT in inclusionDatesList and will be deleted' % (str(snapshot.snapshot_id), str(snapshot.start_time.strftime("%c %Z"))) )
                expiredSnapshots.append(snapshot)
            else:
                self.logger.info('Snapshot %s will be retained per retention policy specified', snapshot.snapshot_id)

        results[EXPIRED_SNAPSHOTS_FOUND] = len(expiredSnapshots)

        # Step #3: Delete snapshots in Collection, unless dryRunFlag is set
        if (self.dryRunFlag == True):
            self.logger.info('Dryrun option is set.  No deletions will occur')

        iteration_count = 0
        iteration_sleep_seconds = 1 * 30      # 30 second backoff
        for snapshot in expiredSnapshots:
            iteration_count += 1
            # Since this loop will generate a large number of API calls within a tight timeframe,
            # we are susceptible to RequestLimitExceeded, which not only affects this process,
            # but all AWS API calls within a region.  As such, we will back off with a sleep every 100 calls
            if( iteration_count % 100 == 0):
                self.logger.info('Backing off '+str(iteration_sleep_seconds)+' seconds every 100 snapshot deletes to avoid API limits.  Zzzzz.....')
                time.sleep(iteration_sleep_seconds)  

            self.logger.info("[#%(idx)s][Snapshot %(vol)s identified for deletion." % {
                    'idx':  str(iteration_count),
                    'vol':  str(snapshot.snapshot_id)
            })
            self.logger.debug("Tags: %(tags)s" % {
                    'tags':  str(snapshot.tags)
            })
            
            try:
                if (self.dryRunFlag == False):
                    snapshot.delete()
                    results[SNAPSHOTS_DELETED] = results[SNAPSHOTS_DELETED] + 1
                else:
                    self.logger.warning('Dryrun is set, snapshot %s will NOT be deleted' % snapshot.snapshot_id)
            except Exception as e:
                self.logger.error('Exception deleting snapshot %s, %s' % (snapshot.snapshot_id, str(e)))
                results[EXCEPTIONS_ENCOUNTERED] = results[EXCEPTIONS_ENCOUNTERED] + 1

        # capture completion time
        finishTime = datetime.now().replace(microsecond=0)

        self.logger.info('Total Snapshots inspected %s', results[TOTAL_SNAPSHOTS_FOUND])
        self.logger.info('Expired Snapshots %s', results[EXPIRED_SNAPSHOTS_FOUND])
        self.logger.info('Deleted Snapshots %s', results[SNAPSHOTS_DELETED])
        self.logger.info('Exceptions Encountered %s', results[EXCEPTIONS_ENCOUNTERED])

        self.logger.info('++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++')
        self.logger.info('++ Completed processing for workload in ' + str(finishTime - startTime) + ' seconds')
        self.logger.info('++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++')


if __name__ == "__main__":

    parser = argparse.ArgumentParser(description='Command line parser')
    parser.add_argument('-r', '--region', help='AWS Region indicator', required=True)
    parser.add_argument('-p', '--policy',
                        help='Retention period for the snapshots. Retention based on the following notation Day:Week:Month eg. 7:5:12 would retain 7 daily, 5 weekly and 12 monthly',
                        required=True)
    parser.add_argument('-k', '--tagKey', help='The name of the Tag Key used for snapshot searches', required=True)
    parser.add_argument('-v', '--tagValue', help='The Tag Value used to match snapshot searches', required=True)
    parser.add_argument('-a', '--account', help='AWS account number', required=True)

    parser.add_argument('-d', '--dryrun', action='count', help='Run but take no Action', required=False)
    parser.add_argument('-l', '--loglevel', choices=['critical', 'error', 'warning', 'info', 'debug', 'notset'],
                        help='The level to record log messages to the logfile', required=False)

    args = parser.parse_args()

    # Log level
    if (args.loglevel > 0):
        loglevel = args.loglevel
    else:
        loglevel = 'info'

    # Dryrun Flag
    if (args.dryrun > 0):
        dryRun = True
    else:
        dryRun = False

    # Launch SnapClean
    policy_split = args.policy.split(":")

    snapCleanMain = SnapClean(args.region, int(policy_split[0]), int(policy_split[1]), int(policy_split[2]),
                              args.tagKey, args.tagValue, args.account, loglevel, dryRun)
    snapCleanMain.execute()
