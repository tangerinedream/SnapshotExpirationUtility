# SnapshotExpirationUtility
This utility allows for easy cleanup of snapshots you deem expired.  The utility identifies all snapshots tagged with a TagKey and TagValue of your choosing, along with an expiration period (in days).

This code can be launched from the command line, or from Lambda (code forthcoming)

## Dependencies
* python 2.7
* boto3
* pytz package
`pip install pytz`


## Usage
```
$ python SnapClean.py -h
usage: SnapClean.py [-h] -r REGION -t TIME -k TAGKEY -v TAGVALUE -a ACCOUNT
                    [-d] [-l {critical,error,warning,info,debug,notset}]

Command line parser

optional arguments:
  -h, --help            show this help message and exit
  -r REGION, --region REGION
                        AWS Region indicator
  -t TIME, --time TIME  Age in days after which snapshot is deleted
  -k TAGKEY, --tagKey TAGKEY
                        The name of the Tag Key used for snapshot searches
  -v TAGVALUE, --tagValue TAGVALUE
                        The Tag Value used to match snapshot searches
  -a ACCOUNT, --account ACCOUNT
                        AWS account number
  -d, --dryrun          Run but take no Action
  -l {critical,error,warning,info,debug,notset}, --loglevel {critical,error,warning,info,debug,notset}
                        The level to record log messages to the logfile
```

### Example: Delete Snapshots over 14 days with TagKey=MakeSnapshot, TagValue=DevTest14
`$ python SnapClean.py -r us-east-1 -t 14 -k MakeSnapshot -v DevTest14 -a 123456789101`

### Example: Dryrun to determine which Snapshots would be deleted (but aren't), including debug level output
`$ python SnapClean.py -r us-east-1 -t 14 -k MakeSnapshot -v DevTest14  -a 123456789101 -l debug -d`