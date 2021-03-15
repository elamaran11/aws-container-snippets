import boto3
from kubernetes import config, client
import time
import botocore
import sys
import os
import yaml
import base64
import re
import requests

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
kubeconfigpath='/tmp/kubeconfig'
configmappath='/tmp/aws-auth-cm.yaml'
workerrolearn = os.environ['WORKER_ROLE_ARN']
lambdarolearn = os.environ['LAMBDA_ROLE_ARN']
userrolearn = os.environ['ADMIN_USER_ROLE_ARN']
clustername = os.environ['CLUSTER_NAME']
url = os.environ['WAIT_CONDITION_URL']
environment = os.environ['APP_ENVIRONMENT']
msgsvcparametername = os.environ['MESSAGE_SERVICE_PARAMETER']
rdspwdparametername = os.environ['RDS_PASSWORD_PARAMETER']
payload = {"Status": "SUCCESS", "Reason": "Setup Complete", "UniqueId": "ID12345", "Data": "EKS cluster configuration complete"}

#The K8s ConfigMap does not support paths in the role ARNs so they are removed in the lines below
workerrolearn = workerrolearn.replace("/app", "")
workerrolearn = workerrolearn.replace("/managed", "")
lambdarolearn = lambdarolearn.replace("/app", "")
lambdarolearn = lambdarolearn.replace("/managed", "")
userrolearn = userrolearn.replace("/app", "")
userrolearn = userrolearn.replace("/managed", "")


###Initialize Boto3 clients
eks = boto3.client('eks',region_name=region)
ssm = boto3.client('ssm',region_name=region)


###Create kubeconfig
def create_kubeconfig():
    serverparameter = clustername+"-server"
    certificateparameter = clustername+"-certificate"
    serverdetails = ssm.get_parameter(Name=serverparameter,WithDecryption=True)['Parameter']['Value']
    certificatedetails = ssm.get_parameter(Name=certificateparameter,WithDecryption=True)['Parameter']['Value']
    print("Creating kubeconfig file at /tmp/kubeconfig")
    kube_content = dict()
    kube_content['apiVersion'] = 'v1'
    kube_content['clusters'] = [
            {
                'cluster':
                {
                    'server': serverdetails,
                    'certificate-authority-data': certificatedetails
                    },
                'name': 'eks-cluster'
                }]
    kube_content['contexts'] = [
            {
                'context':
                {
                    'cluster':'eks-cluster',
                    'user':'lambda'
                    },
                'name':'lambda-context'
                }]
    kube_content['current-context'] = 'lambda-context'
    kube_content['Kind'] = 'Config'
    kube_content['users'] = [
            {
                'name':'lambda',
                'user': {
                        "exec": {
                                "apiVersion": "client.authentication.k8s.io/v1alpha1",
                                "args": ["token", "-i", clustername],
                                "command": "aws-iam-authenticator"
                                }
                        }
                }
            ]
    with open(kubeconfigpath, 'w') as outfile:
        yaml.safe_dump(kube_content, outfile, default_flow_style=False)
    return

#Helper functions to get parameters
def check_encoding(entry):
    try:
        base64.urlsafe_b64decode(entry)
        data = entry
    except:
        data = base64.urlsafe_b64encode(entry.encode('ascii'))
    return data

def get_parameter(parametername):
    parametervalue = ssm.get_parameter(Name=parametername,WithDecryption=True)['Parameter']['Value']
    return parametervalue

def get_b64_parameter(parametername):
    parametervalue = ssm.get_parameter(Name=parametername,WithDecryption=True)['Parameter']['Value']
    b64value = check_encoding(parametervalue)
    return b64value

###Create configmaps
def create_auth_configmap():
    print("Creating configmap file at /tmp/aws-auth-cm.yaml")
    config_content = dict()
    config_content['apiVersion'] = 'v1'
    config_content['kind'] = 'ConfigMap'
    config_content['metadata'] = {
        'name': 'aws-auth',
        'namespace': 'kube-system'
        }
    roles = '''- rolearn: %s
  username: system:node:{{EC2PrivateDNSName}}
  groups:
    - system:bootstrappers
    - system:nodes
- rolearn: %s
  username: lambda-admin
  groups:
    - system:masters
- rolearn: %s
  username: user-assumed-admin
  groups:
    - system:masters''' % (workerrolearn, lambdarolearn, userrolearn)
    config_content['data'] = {
        'mapRoles': roles
        }
                
    with open(configmappath, 'w') as outfile:
            yaml.safe_dump(config_content, outfile, default_flow_style=False)
    return

def create_dfd_namespace(apiclient):
    v1 = client.CoreV1Api(apiclient)

    print("Creating DFD namespace...")
    namespace_content = dict()
    namespace_content['apiVersion'] = 'v1'
    namespace_content['kind'] = 'Namespace'
    namespace_content['metadata'] = {
        'name': 'dfd',
        'labels': {
            'istio-injection': 'enabled'
        }
    }

    v1.create_namespace(body=namespace_content)
    return {'Status': 'DFD namespace created successfully'}

def create_orm_configmap(apiclient):
    v1 = client.CoreV1Api(apiclient)

    print("Creating ORM configmap...")
    orm_content = dict()
    orm_content['apiVersion'] = 'v1'
    orm_content['kind'] = 'ConfigMap'
    orm_content['metadata'] = {
        'name': 'hss-'+environment+'-orm',
        'namespace': 'dfd'
    }
    orm_content['data'] = {
        'FORCE': 'false',
        'ALTER': 'true',
        'LOGGING': 'false'
    }

    v1.create_namespaced_config_map(namespace='dfd', body=orm_content)
    return {'Status': 'ORM configmap created successfully'}


def create_pinpoint_configmap(apiclient):
    v1 = client.CoreV1Api(apiclient)

    print("Creating Pinpoint configmap...")
    appid = get_parameter(environment+'-dfd-pinpoint-app-id')
    senderemail = get_parameter(environment+'-dfd-pinpoint-sender-email')
    pinpoint_content = dict()
    pinpoint_content['apiVersion'] = 'v1'
    pinpoint_content['kind'] = 'ConfigMap'
    pinpoint_content['metadata'] = {
        'name': 'hss-'+environment+'-pinpoint',
        'namespace': 'dfd'
    }
    pinpoint_content['data'] = {
        'APP_ID': appid,
        'AWS_REGION': region,
        'CHANNEL_TYPE': 'APNS',
        'Preferred_Authentication_Method': 'TOKEN',
        'REPLY_EMAIL_ADDRESS': senderemail,
        'SENDER_EMAIL_ADDRESS': senderemail
    }

    v1.create_namespaced_config_map(namespace='dfd', body=pinpoint_content)
    return {'Status': 'Pinpoint configmap created successfully'}

def create_scheduler_configmap(apiclient):
    v1 = client.CoreV1Api(apiclient)

    print("Creating Scheduler configmap...")
    messageurl = get_parameter(msgsvcparametername)
    scheduler_content = dict()
    scheduler_content['apiVersion'] = 'v1'
    scheduler_content['kind'] = 'ConfigMap'
    scheduler_content['metadata'] = {
        'name': 'hss-'+environment+'-scheduler',
        'namespace': 'dfd'
    }
    scheduler_content['data'] = {
        'MESSAGE_SERVICE_URL': messageurl,
        'MESSAGE_SERVICE_PORT': '80',
        'MESSAGE_NOTIFICATION_SERVICE_PATH': '/messaging/push',
        'MESSAGE_EMAIL_SERVICE_PATH': '/messaging/email',
        'MESSAGE_SERVICE_METHOD': 'POST',
        'DB_SCHEMA': 'messaging'
    }

    v1.create_namespaced_config_map(namespace='dfd', body=scheduler_content)
    return {'Status': 'Scheduler configmap created successfully'}

###Get Bearer token
def get_bearer_token():
    STS_TOKEN_EXPIRES_IN = 60
    session = boto3.session.Session()

    client = session.client('sts', region_name=region)
    service_id = client.meta.service_model.service_id

    signer = botocore.signers.RequestSigner(
        service_id,
        region,
        'sts',
        'v4',
        session.get_credentials(),
        session.events
    )

    params = {
        'method': 'GET',
        'url': 'https://sts.{}.amazonaws.com/?Action=GetCallerIdentity&Version=2011-06-15'.format(region),
        'body': {},
        'headers': {
            'x-k8s-aws-id': clustername
        },
        'context': {}
    }

    signed_url = signer.generate_presigned_url(
        params,
        region_name=region,
        expires_in=STS_TOKEN_EXPIRES_IN,
        operation_name=''
    )

    base64_url = base64.urlsafe_b64encode(signed_url.encode('utf-8')).decode('utf-8')

    # remove any base64 encoding padding:
    return 'k8s-aws-v1.' + re.sub(r'=*', '', base64_url)
    
def create_api_client(token):
    config.load_kube_config('/tmp/kubeconfig')
    configuration = client.Configuration()
    configuration.api_key['authorization'] = token
    configuration.api_key_prefix['authorization'] = 'Bearer'
    api = client.ApiClient(configuration)
    return api

#Create services
servicelist = ['node-authentication', 'node-messaging', 'scheduler', 'node-user-activity', 'node-user-survey', 'mvc']

def create_services(apiclient, servicename):
    v1 = client.CoreV1Api(apiclient)
    
    print("Creating "+servicename+" service...")
    service_content = dict()
    service_content['apiVersion'] = 'v1'
    service_content['kind'] = 'Service'
    service_content['metadata'] = {
        'name': 'dfd-'+environment+'-'+servicename+'-srvc',
        'labels': {
            'app': 'dfd-'+environment+'-'+servicename,
            'project': 'digital-front-door',
            'release': '1.0'
        }
    }
    service_content['spec'] = {
        'selector': {
            'app': 'dfd'+environment+'-'+servicename
        },
        'ports': [
            {
                'name': 'http',
                'port': 8080,
                'protocol': 'TCP'
            },
            {
                'name': 'https',
                'port': 443,
                'targetPort': 8080,
                'protocol': 'TCP'
            }
        ]
    }

    v1.create_namespaced_service(namespace='dfd', body=service_content)
    return {'Status': servicename+' service created successfully'}

#Create secrets
def create_db_secret(apiclient):
    v1 = client.CoreV1Api(apiclient)
    print("Creating Database secret...")
    rdsendpoint = get_b64_parameter(environment+'-dfd-rds-host-endpoint')
    rdspwd = get_b64_parameter(rdspwdparametername)
    rdsuser = get_b64_parameter(environment+'-dfd-rds-username')
    db_content = dict()
    db_content['apiVersion'] = 'v1'
    db_content['kind'] = 'Secret'
    db_content['metadata'] = {
        'name': 'hss-'+environment+'-db'
    }
    db_content['type'] = 'Opaque'
    db_content['data'] = {
        'RDS_DATABASE': 'aHNzaGVhbHRo',
        'RDS_HOST': rdsendpoint,
        'RDS_PASSWORD': rdspwd,
        'RDS_USERNAME': rdsuser
    }

    v1.create_namespaced_secret(namespace='dfd', body=db_content)
    return {'Status': environment+'-db secret created successfully'}

###Apply auth configmap
def apply_auth_configmap(apiclient):
    print("Applying aws-auth configmap...")
    v1 = client.CoreV1Api(apiclient)

    with open(configmappath) as f:
        configmap = yaml.load(f, Loader=yaml.FullLoader)
        v1.create_namespaced_config_map(namespace='kube-system', body=configmap)
    return

def continue_cloudformation():
    r = requests.put(url, json=payload)
    return r

def lambda_handler(event, context):
    if not os.path.exists('tmp/kubeconfig'):
        create_kubeconfig()
    if not os.path.exists('/tmp/aws-auth-cm.yaml'):
        create_auth_configmap()
    token = get_bearer_token()
    apiclient = create_api_client(token)
    try:
        print("Creating aws-auth configmap...")
        apply_auth_configmap(apiclient)
    except:
        print("Confimap aws-auth already exists. Skipping configmap creation...")
    try:
        print("Creating dfd namespace...")
        create_dfd_namespace(apiclient)
    except:
        print("Namespace dfd already exists. Skipping namespace creation...")
    try:
        print("Creating ORM configmap...")
        create_orm_configmap(apiclient)
    except:
        print("Confimap "+environment+"-orm already exists. Skipping configmap creation...")
    try:
        print("Creating Pinpoint configmap...")
        create_pinpoint_configmap(apiclient)
    except:
        print("Confimap "+environment+"-pinpoint already exists. Skipping configmap creation...")
    try:
        print("Creating Scheduler configmap...")
        create_scheduler_configmap(apiclient)
    except:
        print("Confimap "+environment+"-scheduler already exists. Skipping configmap creation...")
    try:
        print("Creating DB secret...")
        create_db_secret(apiclient)
    except:
        print("DB secret already exists. Skipping secret creation...")
    for x in servicelist:
        try:
            print("Creating dfd-"+environment+"-"+x+"-srvc...")
            create_services(apiclient, x)
        except:
            print("dfd-"+environment+"-"+x+"-srvc already exists. Skipping ...")
    continue_cloudformation()
    return {'Status': 'Success'}