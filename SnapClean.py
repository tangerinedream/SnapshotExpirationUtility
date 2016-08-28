#!/usr/bin/python

import boto3
import datetime



class SnapClean(object):
    def __init__(self, region, retentionTime, tagKey, tagValue, logLevel, dryRunFlag):
        self.region = region
        self.retentionTime = retentionTime
        self.tagKey = tagKey
        self.tagValue = tagValue
        self.logLevel = logLevel
        self.dryRunFlag = dryRunFlag

        self.currDateTime = datetime.datetime.now()
        self.expirationDateTime = self.currDateTime - datetime.timedelta(days=self.retentionTime)


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

        filenameVal = 'SnapClean.log'

        logging.basicConfig(format='%(asctime)s:%(levelname)s:%(name)s==>%(message)s\n',
                            filename=filenameVal,
                            level=loggingLevelSelected)

        # Add the rotating file handler
        handler = logging.handlers.RotatingFileHandler(
            filename=filenameVal,
            mode='a',
            maxBytes=1024 * 1024,
            backupCount=30)

        self.logger.addHandler(handler)

        # Setup the Handlers
        # create console handler and set level to debug
        consoleHandler = logging.StreamHandler()
        consoleHandler.setLevel(logging.INFO)
        self.logger.addHandler(consoleHandler)

    def execute(self):

        # Get the EC2 Service Resource
        ec2ServiceResourceAPI = boto3.resource('ec2', region_name=self.region)
        snapshotAPI = ec2ServiceResourceAPI.Snapshot('id')


        # Step 1: Get all snapshots matching the Tag/Value
        targetFilter = [
            {
                'Name': 'state',
                'Values': ['completed']
            },
            {
                'Name': 'tag:' + self.tagKey,
                'Values': [self.tagValue]
            },
        ]

        snapshot_iterator=iter([])  # empty iter for scope purposes
        try:
            snapshot_iterator = snapshotAPI.filter(
                Filters=targetFilter
            )
        except Exception as e:
            self.logger.error('Exception filtering snapshots %s' + str(e))

        # Step #2: Collect all snapshots older than retentionTime
        expiredSnapshots = []
        for snapshot in snapshot_iterator:
            assert isinstance(snapshot.start_time, datetime), '%r is not a datetime' % snapshot.start_time
            self.logger.debug('Found snapshot matching tag: %s', snapshot.snapshot_id)
            snapshotStartDateTime = snapshot.start_time
            # if snapshot start_time is older than the expiration retention date, add it to the expired list
            if(snapshotStartDateTime < self.expirationDateTime):
                expiredSnapshots.append(snapshot)

        # Step #3: Delete snapshots in Collection, unless dryRunFlag is set
        if(self.dryRunFlag == False ):
            for snapshot in expiredSnapshots:
                self.logger.info('Deleting snapshot %s' % snapshot.snapshot_id)
                try:
                    snapshot.delete()
                except Exception as e:
                    self.logger.error('Exception deleting snapshot %s, %s' % (snapshot.snapshot_id, str(e)))
        else
            self.logger.warning('Dryrun flag is set, no snapshots will be deleted')




if __name__ == "__main__":
    # python SnapClean.py -r us-east-1 -t 14 -k tagKey -v tagValue -d

    parser = argparse.ArgumentParser(description='Command line parser')
    parser.add_argument('-r', '--region', help='AWS Region indicator', required=True)
    parser.add_argument('-t', '--time', help='Age in days after which snapshot is deleted', required=True)
    parser.add_argument('-k', '--tagKey', help='The name of the Tag Key used for snapshot searches', required=True)
    parser.add_argument('-v', '--tagValue', help='The Tag Value used to match snapshot searches', required=True)
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
    snapCleanMain = SnapClean(args.region, args.time, args.tagKey, args.tagValue, loglevel, dryRun)
    snapCleanMain.execute()