# SnapshotExpirationUtility
This utility allows for easy cleanup of snapshots you deem expired.  The utility identifies all snapshots tagged with a TagKey and TagValue of your choosing, along with an expiration period (in days).

This code can be launched from the command line, or from Lambda (code forthcoming)

## Dependencies
* python 2.7
* boto3 package
* pytz package
* grandfatherson package

`sudo pip install boto3 pytz grandfatherson`


## Usage
*Please note:* 
* Weekly is assumed to be Saturday snapshots
* Monthly are assumed to be first of the month snapshots
```
$ python SnapClean.py --help
usage: SnapClean.py [-h] -r REGION -p POLICY -k TAGKEY -v TAGVALUE -a ACCOUNT
                    [-d] [-l {critical,error,warning,info,debug,notset}]

Command line parser

optional arguments:
  -h, --help            show this help message and exit
  -r REGION, --region REGION
                        AWS Region indicator
  -p POLICY, --policy POLICY
                        Retention period for the snapshots. Retention based on
                        the following notation Day:Week:Month eg. 7:5:12 would
                        retain 7 daily, 5 weekly and 12 monthly
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

### Example: Delete Snapshots in us-east-1 with TagKey=MakeSnapshot, TagValue=DevTest14 which are older than 14 days (Policy is 14 Daily, 0 Weekly, 0 Monthly)
#### Note: This removes all Daily snapshots older than 14 days
`$ python SnapClean.py -r us-east-1 -p 14:0:0 -k MakeSnapshot -v DevTest14 -a 123456789101`

### Example: Delete Snapshots in eu-west-1 with TagKey=MakeSnapshot, TagValue=True which are older than the following policy : 7 Daily, 5 Weekly, 12 Monthly).
#### Note: This removes all Daily snapshots older than 7 days, all Weekly older than 5 weeks, and all Monthly older than 12 months
`$ python SnapClean.py -r us-east-1 -p 14:0:0 -k MakeSnapshot -v DevTest14 -a 123456789101`

### Example: With DEBUG level logging, delete Snapshots in eu-west-1 with TagKey=MakeSnapshot, TagValue=True which are older than the following policy : 7 Daily, 5 Weekly, 12 Monthly).
#### Note: This removes all Daily snapshots older than 7 days, all Weekly older than 5 weeks, and all Monthly older than 12 months
`$ python SnapClean.py -r us-east-1 -p 14:0:0 -k MakeSnapshot -v DevTest14 -a 123456789101` -l debug

