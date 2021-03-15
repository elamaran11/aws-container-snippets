import boto3
import time
import botocore
import sys
import os

###Helper function to turn environment variable string into list
def split_string(x):
    y = x.replace("[", "")
    y = y.replace("]", "")
    y = y.replace(" ", "")
    y = y.replace("'", "")
    y = y.strip()
    y = y.split(',')
    return y

##Parameter definitions
region = os.environ['AWS_REGION']
clusterrolearn = os.environ['CLUSTER_ROLE_ARN']
clustername = os.environ['CLUSTER_NAME']
subnets = split_string(os.environ['SUBNET_IDS'])
securitygroups = split_string(os.environ['SECURITY_GROUP_IDS'])
k8sversion = os.environ['KUBERNETES_VERSION']
kmskey = os.environ['KMS_KEY_ARN']
configfunction = os.environ['CONFIG_LAMBDA_ARN']
environment = os.environ['APP_ENVIRONMENT']

###Initialize Boto3 clients
eks = boto3.client('eks',region_name=region)
lambdaclient = boto3.client('lambda',region_name=region)
ssm = boto3.client('ssm',region_name=region)
pinpoint = boto3.client('pinpoint',region_name=region)
cognito = boto3.client('cognito-idp',region_name=region)
rds = boto3.client('rds',region_name=region)

###Check if cluster already exists
def clusterexists():
    print("Checking if EKS cluster: %s exists..." % clustername)
    try:
        eks.describe_cluster(name=clustername)
        existence = True
        print("Cluster exists.")
    except:
        existence = False
        print("Cluster does not exist.")
    return existence

###Build EKS Cluster
def buildcluster():
    print('Initiating EKS cluster build...')
    eks.create_cluster(
        name=clustername,
        version=k8sversion,
        roleArn=clusterrolearn,
        resourcesVpcConfig={
            'subnetIds': subnets,
            'securityGroupIds': securitygroups,
            'endpointPublicAccess': False,
            'endpointPrivateAccess': True
        },
        logging={
            'clusterLogging': [
                {
                    'types': [
                        'api',
                        'audit',
                        'authenticator',
                        'controllerManager',
                        'scheduler'
                    ],
                    'enabled': True
                }
            ]
        },
        tags={
            'Name': clustername
        },
    encryptionConfig=[
        {
            'resources': [
                'secrets'
            ],
            'provider': {
                'keyArn': kmskey
            }
        }
    ]
    )
    print('Cluster build initiated...')
    return
    
###Check EKS Cluster status
def checkclusterstatus():
    status=''
    readystatus='ACTIVE'

    while status != readystatus:
        print("Checking cluster status...")
        time.sleep(30)
        response = eks.describe_cluster(name=clustername)
        status = response["cluster"]["status"]
        print("Cluster status: %s" % status)

    endpoint = response["cluster"]["endpoint"]
    certificate = response["cluster"]["certificateAuthority"]["data"]
    return (endpoint, certificate)

###Put Cluster Connection Details in Parameter Store
def storeeksdetails(clusterdetails):
    serverparameter = clustername+"-server"
    certificateparameter = clustername+"-certificate"
    try:
        serverstatus = ssm.get_parameter(Name=serverparameter,WithDecryption=True)
        serverdetails = serverstatus['Parameter']['Value']
        if serverdetails != clusterdetails[0]:
            serverstatus = ssm.put_parameter(
                Name=serverparameter,
                Description='Server details for EKS cluster: '+clustername,
                Value=clusterdetails[0],
                Type='SecureString',
                KeyId=kmskey,
                Overwrite=True,
                Tier='Standard' 
            )
            serverdetails = clusterdetails[0]
    except:
        serverstatus = ssm.put_parameter(
            Name=serverparameter,
            Description='Server details for EKS cluster: '+clustername,
            Value=clusterdetails[0],
            Type='SecureString',
            KeyId=kmskey,
            Tier='Standard'
        )
        serverdetails = clusterdetails[0]
    try:
        certificatestatus = ssm.get_parameter(Name=certificateparameter,WithDecryption=True)
        certificatedetails = certificatestatus['Parameter']['Value']
        if certificatedetails != clusterdetails[1]:
            certificatestatus = ssm.put_parameter(
                Name=certificateparameter,
                Description='Certificate details for EKS cluster: '+clustername,
                Value=clusterdetails[1],
                Type='SecureString',
                KeyId=kmskey,
                Overwrite=True,
                Tier='Standard' 
            )
            certificatedetails = clusterdetails[1]
    except:
        certificatestatus = ssm.put_parameter(
            Name=certificateparameter,
            Description='Certificate details for EKS cluster: '+clustername,
            Value=clusterdetails[1],
            Type='SecureString',
            KeyId=kmskey,
            Tier='Standard'
        )
        certificatedetails = clusterdetails[1]
    return (serverdetails,certificatedetails)

##Store values in parameter store
def store_parameters(name,description,value):
    try:
        status = ssm.get_parameter(Name=name,WithDecryption=True)
        details = status['Parameter']['Value']
        if details != value:
            ssm.put_parameter(
                Name=name,
                Description=description,
                Value=value,
                Type='SecureString',
                KeyId=kmskey,
                Overwrite=True,
                Tier='Standard' 
            )
    except:
        ssm.put_parameter(
            Name=name,
            Description=description,
            Value=value,
            Type='SecureString',
            KeyId=kmskey,
            Overwrite=True,
            Tier='Standard' 
        )

##Store Pinpoint Details
def store_pinpoint_parameters():
    response = pinpoint.get_apps()
    items = response['ApplicationsResponse']['Item']
    for x in items:
        name = x['Name']
        if name == 'dfd-pinpoint':
            appid = x['Id']
    store_parameters(environment+'-dfd-pinpoint-app-id','Pinpoint App Id for DFD application',appid)
    emailresponse = pinpoint.get_email_channel(ApplicationId=appid)
    senderemail = emailresponse['EmailChannelResponse']['FromAddress']
    store_parameters(environment+'-dfd-pinpoint-sender-email','Pinpoint sender email address',senderemail)

##Store Cognito Details
def store_cognito_parameters():
    poolresponse = cognito.list_user_pools(MaxResults=20)
    pools = poolresponse['UserPools']
    for x in pools:
        name = x['Name']
        if name == 'dfd-'+environment+'-pool':
            id = x['Id']
    store_parameters(environment+'-dfd-cognito-pool-id','ID of Cognito DFD user pool',id)
    clientresponse = cognito.list_user_pool_clients(UserPoolId=id)
    clients = clientresponse['UserPoolClients']
    for x in clients:
        name = x['ClientName']
        if name == 'dfd-cognito-'+environment:
            clientid = x['ClientId']
    store_parameters(environment+'-dfd-cognito-client-id','ID of Cognito DFD app client',clientid)

##Store RDS Details
def store_rds_parameters():
    clusterresponse = rds.describe_db_clusters(DBClusterIdentifier='dfd-'+environment+'-aurora')
    hostname = clusterresponse['DBClusters'][0]['Endpoint']
    username = clusterresponse['DBClusters'][0]['MasterUsername']
    store_parameters(environment+'-dfd-rds-host-endpoint','RDS cluster endpoint',hostname)
    store_parameters(environment+'-dfd-rds-username','RDS cluster master username',username)

###Invoke Lambda function to update configmap
def configurationtrigger():
    lambdaclient.invoke(
        FunctionName=configfunction,
        InvocationType='RequestResponse',
        LogType='Tail'
    )

def lambda_handler(event, context):
    existence = clusterexists()
    if existence == False:
        buildcluster()
    connectiondetails = checkclusterstatus()
    storeeksdetails(connectiondetails)
    store_pinpoint_parameters()
    store_cognito_parameters()
    store_rds_parameters()
    configurationtrigger()
    return {'Status': 'Success'}
