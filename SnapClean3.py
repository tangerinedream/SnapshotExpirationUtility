#!/usr/bin/env python3

from __future__ import print_function

import argparse
import logging
import logging.handlers
import time
import sys
from datetime import datetime, timedelta
import pytz
import boto3
from dateutil.relativedelta import relativedelta
from grandfatherson import dates_to_keep, SATURDAY
from redo import retriable, retry


class SnapClean(object):
    DATE_FORMAT = "%a %b %d %Y"

    def __init__(self,
                 region,
                 policyDay,
                 policyWeek,
                 policyMonth,
                 tagKey,
                 tagValue,
                 account,
                 logLevel,
                 dryRunFlag):

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
        log_formatter = logging.Formatter(
            '[%(asctime)s][%(levelname)s]%(message)s'
            )

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

        # Calculate the date range where snapshots will
        # be evaluated against by the grandfatherson algorithm.
        if self.policyMonth > 0:
            start_range_date = end_range_date - relativedelta(
                months=self.policyMonth)
        elif self.policyWeek > 0:
            start_range_date = end_range_date - relativedelta(
                weeks=self.policyWeek)
        elif self.policyDay > 0:
            start_range_date = end_range_date - relativedelta(
                days=self.policyDay)
        else:
            self.logger.error('error: no policy values specified, exiting')
            sys.exit(-1)

        self.logger.info('Retention Policy: %(daily)s Daily, %(weekly)s Weekly, and %(monthly)s Monthly' % {
            'daily': self.policyDay,
            'weekly': self.policyWeek,
            'monthly': self.policyMonth
        })
        self.logger.info('Start of Range date in UTC: ' + start_range_date.strftime(SnapClean.DATE_FORMAT))
        self.logger.info('End of Range date in UTC: ' + end_range_date.strftime(SnapClean.DATE_FORMAT))

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

    def snsInit(self):
        sns_topic_name = "SnapClean"
        self.sns = SnsNotifier(sns_topic_name)

    @retriable(attempts=5, sleeptime=15, jitter=0)
    def getFilteredSnapshots(self):
        ec2 = boto3.resource("ec2", region_name=self.region)
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

        try:
            snapshot_iterator = ec2.snapshots.filter(Filters=targetFilter)
            return snapshot_iterator
        except Exception as e:
            msg = "Exception filtering snapshots %s" % (str(e))
            self.logger.error(msg)
            snsSubject = 'SnapClean.py : Exception filtering snapshots'
            snsMessage = '%s' % (str(e))
            self.sns.sendSns(snsSubject, snsMessage)

    @retriable(attempts=5, sleeptime=15, jitter=0)
    ### Return a list of snapshots associated with an AMI.
    def getInUseSnapshots(self):
        ec2 = boto3.resource("ec2", region_name=self.region)
        imageFilter = [
            {
                'Name': 'owner-id',
                'Values': [self.account]
            }
        ]

        InUseSnapShots = []

        try:
            images = ec2.images.filter(Filters=imageFilter).all()
            for image in images:
                for snapshots in image.block_device_mappings:
                    if 'Ebs' in snapshots:
                        self.logger.info('Snapshot %s is in use with AMI %s', snapshots['Ebs']['SnapshotId'], image.id)
                        InUseSnapShots.append(snapshots['Ebs']['SnapshotId'])
            return InUseSnapShots
        except Exception as e:
            msg = "Exception filtering in-use snapshots %s" % (str(e))
            self.logger.error(msg)
            snsSubject = 'SnapClean.py : Exception filtering in-use snapshots'
            snsMessage = '%s' % (str(e))
            self.sns.sendSns(snsSubject, snsMessage)

    @retriable(attempts=5, sleeptime=5, jitter=0)
    def deleteSnapshot(self, snapshot):
        snapshot.delete()

    def execute(self):
        # Log the duration of the processing
        startTime = datetime.now().replace(microsecond=0)

        self.logger.info('============================================================================')

        snapshot_iterator = iter([])
        snapshot_iterator = self.getFilteredSnapshots()

        # Step #2: Collect all snapshots older than retentionTime
        TOTAL_SNAPSHOTS_FOUND = 'totalSnapshotsFound'
        EXPIRED_SNAPSHOTS_FOUND = 'expiredSnapshotsFound'
        SNAPSHOTS_DELETED = 'deletedSnapshots'
        EXCEPTIONS_ENCOUNTERED = 'exceptionsEncountered'
        TOTAL_SNAPSHOTS_ASSOCIATED_WITH_AMIS = 'totalSnapshotsAssociatedWithAMIs'
        TOTAL_INSCOPE_SNAPSHOTS_ASSOCIATED_WITH_AMIS = 'totalInscopeSnapshotsAssociatedWithAMIs'

        results = { TOTAL_SNAPSHOTS_FOUND: 0,
                    EXPIRED_SNAPSHOTS_FOUND: 0,
                    SNAPSHOTS_DELETED: 0,
                    EXCEPTIONS_ENCOUNTERED: 0,
                    TOTAL_SNAPSHOTS_ASSOCIATED_WITH_AMIS: 0,
                    TOTAL_INSCOPE_SNAPSHOTS_ASSOCIATED_WITH_AMIS: 0 }

        # Go get a list of current snapshots associated to an AMI
        in_use_snapshots = self.getInUseSnapshots()
        results[TOTAL_SNAPSHOTS_ASSOCIATED_WITH_AMIS] = len(in_use_snapshots)
                
        expiredSnapshots = []

        inclusionDatesList = self.generateInclusionDatesList()

        for item in inclusionDatesList:
            self.logger.info(item.strftime(SnapClean.DATE_FORMAT))

        inscope_inuse_snapshots = []
        inscope_inuse_snapshots_count = 0

        for snapshot in snapshot_iterator:
            # If the snapshot isn't in the in_use_snapshots list, continue.
            if (snapshot.id not in str(in_use_snapshots)):
                self.logger.info('Snapshot %s is in-scope.', snapshot.id)
                assert isinstance(snapshot.start_time, datetime), '%r is not a datetime' % snapshot.start_time
                results[TOTAL_SNAPSHOTS_FOUND] = results[TOTAL_SNAPSHOTS_FOUND] + 1
                snapshotStartDateTime = snapshot.start_time.date()
                if (snapshotStartDateTime not in inclusionDatesList):
                    self.logger.info('Snapshot %s with date %s is NOT in inclusionDatesList and will be deleted' % (str(snapshot.snapshot_id), str(snapshot.start_time.strftime("%c %Z"))))
                    expiredSnapshots.append(snapshot)
                else:
                    self.logger.info('Snapshot %s will be retained per retention policy specified', snapshot.snapshot_id)
            else:
                self.logger.info('Inscope Snapshot %s is currently in use with an AMI. ( %s )', snapshot.snapshot_id, snapshot.description)
                inscope_inuse_snapshots.append(snapshot)

        results[TOTAL_INSCOPE_SNAPSHOTS_ASSOCIATED_WITH_AMIS] = len(inscope_inuse_snapshots)
        results[EXPIRED_SNAPSHOTS_FOUND] = len(expiredSnapshots)

        # Step #3: Delete snapshots in Collection, unless dryRunFlag is set
        if (self.dryRunFlag is True):
            self.logger.info('Dryrun option is set.  No deletions will occur')

        iteration_count = 0
        iteration_sleep_seconds = 1 * 30      # 30 second backoff
        for snapshot in expiredSnapshots:
            iteration_count += 1
            # Since this loop will generate a large number of API calls within a tight timeframe,
            # we are susceptible to RequestLimitExceeded, which not only affects this process,
            # but all AWS API calls within a region.  As such, we will back off with a sleep every 100 calls
            if(iteration_count % 70 == 0):
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
                if (self.dryRunFlag is False):
                    self.deleteSnapshot(snapshot)
                    results[SNAPSHOTS_DELETED] = results[SNAPSHOTS_DELETED] + 1
                else:
                    self.logger.warning('Dryrun is set, snapshot %s will NOT be deleted' % snapshot.snapshot_id)
            except Exception as e:
                msg = "Exception deleting snapshot %s, %s" % (snapshot.snapshot_id, str(e))
                self.logger.error(msg)
                snsSubject = 'SnapClean.py : Exception deleting snapshot %s' % (snapshot.snapshot_id)
                snsMessage = '%s' % (str(e))
                self.sns.sendSns(snsSubject, snsMessage)
                results[EXCEPTIONS_ENCOUNTERED] = results[EXCEPTIONS_ENCOUNTERED] + 1

        # capture completion time
        finishTime = datetime.now().replace(microsecond=0)

        self.logger.info('============================================================================')
        self.logger.info('Total Snapshots inspected %s', results[TOTAL_SNAPSHOTS_FOUND])
        self.logger.info('Expired Snapshots %s', results[EXPIRED_SNAPSHOTS_FOUND])
        self.logger.info('Deleted Snapshots %s', results[SNAPSHOTS_DELETED])
        self.logger.info('Total Snapshots in use with AMIs : %s', results[TOTAL_SNAPSHOTS_ASSOCIATED_WITH_AMIS])
        self.logger.info('Total In-scope Snapshots currently in use with an AMI : %s', results[TOTAL_INSCOPE_SNAPSHOTS_ASSOCIATED_WITH_AMIS])
        self.logger.info('Exceptions Encountered %s', results[EXCEPTIONS_ENCOUNTERED])
        self.logger.info('++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++')
        self.logger.info('++ Completed processing for workload in ' + str(finishTime - startTime) + ' seconds')
        self.logger.info('++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++')


class SnsNotifier(object):
    def __init__(self, topic):
        self.topic = topic

    @retriable(attempts=5, sleeptime=10, jitter=0)
    def sendSns(self, subject, message):
        client = boto3.resource('sns')
        topic = client.create_topic(Name=self.topic)
        topic.publish(Subject=subject, Message=str(message))


if __name__ == "__main__":

    parser = argparse.ArgumentParser(description='Command line parser')
    parser.add_argument('-r', '--region',
                        help='AWS Region indicator', required=True)
    parser.add_argument('-p', '--policy',
                        help='Retention period for the snapshots. Retention based on the following notation Day:Week:Month eg. 7:5:12 would retain 7 daily, 5 weekly and 12 monthly',
                        required=True)
    parser.add_argument('-k', '--tagKey',
                        help='The name of the Tag Key used for snapshot searches',
                        required=True)
    parser.add_argument('-v', '--tagValue',
                        help='The Tag Value used to match snapshot searches',
                        required=True)
    parser.add_argument('-a', '--account',
                        help='AWS account number',
                        required=True)
    parser.add_argument('-d', '--dryrun', action='count',
                        help='Run but take no Action',
                        required=False)
    parser.add_argument('-l', '--loglevel',
                        choices=[
                            'critical',
                            'error',
                            'warning',
                            'info',
                            'debug',
                            'notset'
                            ],
                        help='The level to record log messages to the logfile',
                        required=False)

    args = parser.parse_args()

    # Log level
    if args.loglevel:
        loglevel = args.loglevel
    else:
        loglevel = 'info'

    # Dryrun Flag
    if args.dryrun:
        dryRun = True
    else:
        dryRun = False

    # Launch SnapClean
    policy_split = args.policy.split(":")

    snapCleanMain = SnapClean(
        args.region,
        int(policy_split[0]),
        int(policy_split[1]),
        int(policy_split[2]),
        args.tagKey,
        args.tagValue,
        args.account,
        loglevel,
        dryRun
        )

    snapCleanMain.snsInit()

    snapCleanMain.execute()

