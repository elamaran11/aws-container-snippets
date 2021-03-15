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

##Parameter definitions
region = os.environ['AWS_REGION']
kubeconfigpath='/tmp/kubeconfig'
clustername = os.environ['CLUSTER_NAME']
environment = os.environ['APP_ENVIRONMENT']
accountnumber = os.environ['ACCOUNT_NUMBER']
imagebase = accountnumber+'.dkr.ecr.'+region+'.amazonaws.com/'

###Initialize Boto3 clients
ssm = boto3.client('ssm',region_name=region)

###Create kubeconfig
def createkubeconfig(ssm=ssm,kubeconfigpath=kubeconfigpath,clustername=clustername,region=region):
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

def get_bearer_token(clustername=clustername, region=region):
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

def get_deployment(apiclient, repositoryname, namespace='dfd'):
    v1 = client.AppsV1Api(apiclient)

    deploymentname = repositoryname+'-deployment'
    v1.read_namespaced_deployment(name=deploymentname, namespace=namespace)
    return

def get_cron_job(apiclient, repositoryname, namespace='dfd'):
    v1 = client.BatchV1beta1Api(apiclient)

    v1.read_namespaced_cron_job(name=repositoryname, namespace=namespace)
    return

def create_user_activity_deployment(apiclient, imagetag, repositoryname, imagebase=imagebase):
    v1 = client.AppsV1Api(apiclient)
    deploymentname = repositoryname+'-deployment'

    payload = dict()
    payload['apiVersion'] = "apps/v1"
    payload['kind'] = "Deployment"
    payload['metadata'] = {
        "name": deploymentname,
		"namespace": "dfd"
    }
    payload['spec'] = {
        'replicas' : 1,
        'selector': {
            'matchLabels': {
                'app': repositoryname
            }
        },
        'strategy': {
            'rollingUpdate': {
                'maxSurge': 4,
                'maxUnavailable': 0
            }
        },
        'template': {
            'metadata': {
                'labels': {
                    'app': repositoryname,
                    'project': 'digital-front-door',
                    'release': '1.0'
                }
            },
            'spec': {
                'containers': [
                    {
                        'image': imagebase+repositoryname+':'+imagetag,
                        'name': repositoryname,
                        'command': [
                            'npm',
                            'start'
                        ],
                        'imagePullPolicy': 'Always',
                        'ports': [
                            {
                                'containerPort': 8080
                            }
                        ],
                        'resources': {
                            'limits': {
                                'cpu': '2000m',
                                'memory': '1024Mi'
                            },
                            'requests': {
                                'cpu': '100m',
                                'memory': '128Mi'
                            }
                        },
                        'env': [
                            {
                                'name': 'RDS_USERNAME',
                                'valueFrom': {
                                    'secretKeyRef': {
                                        'name': 'hss-'+environment+'-db',
                                        'key': 'RDS_USERNAME'
                                    }
                                }
                            },
                            {
                                'name': 'RDS_PASSWORD',
                                'valueFrom': {
                                    'secretKeyRef': {
                                        'name': 'hss-'+environment+'-db',
                                        'key': 'RDS_PASSWORD'
                                    }
                                }
                            },
                            {
                                'name': 'RDS_HOST',
                                'valueFrom': {
                                    'secretKeyRef': {
                                        'name': 'hss-'+environment+'-db',
                                        'key': 'RDS_HOST'
                                    }
                                }
                            },
                            {
                                'name': 'RDS_DATABASE',
                                'valueFrom': {
                                    'secretKeyRef': {
                                        'name': 'hss-'+environment+'-db',
                                        'key': 'RDS_DATABASE'
                                    }
                                }
                            },
                            {
                                'name': 'FORCE',
                                'valueFrom': {
                                    'configMapKeyRef': {
                                        'name': 'hss-'+environment+'-orm',
                                        'key': 'FORCE'
                                    }
                                }
                            },
                            {
                                'name': 'ALTER',
                                'valueFrom': {
                                    'configMapKeyRef': {
                                        'name': 'hss-'+environment+'-orm',
                                        'key': 'ALTER'
                                    }
                                }
                            },
                            {
                                'name': 'LOGGING',
                                'valueFrom': {
                                    'configMapKeyRef': {
                                        'name': 'hss-'+environment+'-orm',
                                        'key': 'LOGGING'
                                    }
                                }
                            }
                        ]
                    }
                ]
            }
        }
    }

    v1.create_namespaced_deployment(namespace='dfd', body=payload)
    return

def create_user_survey_deployment(apiclient, imagetag, repositoryname, imagebase=imagebase):
    v1 = client.AppsV1Api(apiclient)
    deploymentname = repositoryname+'-deployment'

    payload = dict()
    payload['apiVersion'] = "apps/v1"
    payload['kind'] = "Deployment"
    payload['metadata'] = {
        "name": deploymentname,
		"namespace": "dfd"
    }
    payload['spec'] = {
        'replicas' : 1,
        'selector': {
            'matchLabels': {
                'app': repositoryname
            }
        },
        'strategy': {
            'rollingUpdate': {
                'maxSurge': 4,
                'maxUnavailable': 0
            }
        },
        'template': {
            'metadata': {
                'labels': {
                    'app': repositoryname,
                    'project': 'digital-front-door',
                    'release': '1.0'
                }
            },
            'spec': {
                'containers': [
                    {
                        'image': imagebase+repositoryname+':'+imagetag,
                        'name': repositoryname,
                        'command': [
                            'npm',
                            'start'
                        ],
                        'imagePullPolicy': 'Always',
                        'ports': [
                            {
                                'containerPort': 8080
                            }
                        ],
                        'resources': {
                            'limits': {
                                'cpu': '2000m',
                                'memory': '1024Mi'
                            },
                            'requests': {
                                'cpu': '100m',
                                'memory': '128Mi'
                            }
                        },
                        'env': [
                            {
                                'name': 'RDS_USERNAME',
                                'valueFrom': {
                                    'secretKeyRef': {
                                        'name': 'hss-'+environment+'-db',
                                        'key': 'RDS_USERNAME'
                                    }
                                }
                            },
                            {
                                'name': 'RDS_PASSWORD',
                                'valueFrom': {
                                    'secretKeyRef': {
                                        'name': 'hss-'+environment+'-db',
                                        'key': 'RDS_PASSWORD'
                                    }
                                }
                            },
                            {
                                'name': 'RDS_HOST',
                                'valueFrom': {
                                    'secretKeyRef': {
                                        'name': 'hss-'+environment+'-db',
                                        'key': 'RDS_HOST'
                                    }
                                }
                            },
                            {
                                'name': 'RDS_DATABASE',
                                'valueFrom': {
                                    'secretKeyRef': {
                                        'name': 'hss-'+environment+'-db',
                                        'key': 'RDS_DATABASE'
                                    }
                                }
                            },
                            {
                                'name': 'FORCE',
                                'valueFrom': {
                                    'configMapKeyRef': {
                                        'name': 'hss-'+environment+'-orm',
                                        'key': 'FORCE'
                                    }
                                }
                            },
                            {
                                'name': 'ALTER',
                                'valueFrom': {
                                    'configMapKeyRef': {
                                        'name': 'hss-'+environment+'-orm',
                                        'key': 'ALTER'
                                    }
                                }
                            },
                            {
                                'name': 'LOGGING',
                                'valueFrom': {
                                    'configMapKeyRef': {
                                        'name': 'hss-'+environment+'-orm',
                                        'key': 'LOGGING'
                                    }
                                }
                            }
                        ]
                    }
                ]
            }
        }
    }

    v1.create_namespaced_deployment(namespace='dfd', body=payload)
    return

def create_messaging_deployment(apiclient, imagetag, repositoryname, imagebase=imagebase):
    v1 = client.AppsV1Api(apiclient)
    deploymentname = repositoryname+'-deployment'

    payload = dict()
    payload['apiVersion'] = "apps/v1"
    payload['kind'] = "Deployment"
    payload['metadata'] = {
        "name": deploymentname,
		"namespace": "dfd"
    }
    payload['spec'] = {
        'replicas' : 1,
        'selector': {
            'matchLabels': {
                'app': repositoryname
            }
        },
        'strategy': {
            'rollingUpdate': {
                'maxSurge': 4,
                'maxUnavailable': 0
            }
        },
        'template': {
            'metadata': {
                'labels': {
                    'app': repositoryname,
                    'project': 'digital-front-door',
                    'release': '1.0'
                }
            },
            'spec': {
                'containers': [
                    {
                        'image': imagebase+repositoryname+':'+imagetag,
                        'name': repositoryname,
                        'command': [
                            'npm',
                            'start'
                        ],
                        'imagePullPolicy': 'Always',
                        'ports': [
                            {
                                'containerPort': 8080
                            }
                        ],
                        'resources': {
                            'limits': {
                                'cpu': '2000m',
                                'memory': '1024Mi'
                            },
                            'requests': {
                                'cpu': '100m',
                                'memory': '128Mi'
                            }
                        },
                        'env': [
                            {
                                'name': 'RDS_USERNAME',
                                'valueFrom': {
                                    'secretKeyRef': {
                                        'name': 'hss-'+environment+'-db',
                                        'key': 'RDS_USERNAME'
                                    }
                                }
                            },
                            {
                                'name': 'RDS_PASSWORD',
                                'valueFrom': {
                                    'secretKeyRef': {
                                        'name': 'hss-'+environment+'-db',
                                        'key': 'RDS_PASSWORD'
                                    }
                                }
                            },
                            {
                                'name': 'RDS_HOST',
                                'valueFrom': {
                                    'secretKeyRef': {
                                        'name': 'hss-'+environment+'-db',
                                        'key': 'RDS_HOST'
                                    }
                                }
                            },
                            {
                                'name': 'RDS_DATABASE',
                                'valueFrom': {
                                    'secretKeyRef': {
                                        'name': 'hss-'+environment+'-db',
                                        'key': 'RDS_DATABASE'
                                    }
                                }
                            },
                            {
                                'name': 'FORCE',
                                'valueFrom': {
                                    'configMapKeyRef': {
                                        'name': 'hss-'+environment+'-orm',
                                        'key': 'FORCE'
                                    }
                                }
                            },
                            {
                                'name': 'ALTER',
                                'valueFrom': {
                                    'configMapKeyRef': {
                                        'name': 'hss-'+environment+'-orm',
                                        'key': 'ALTER'
                                    }
                                }
                            },
                            {
                                'name': 'LOGGING',
                                'valueFrom': {
                                    'configMapKeyRef': {
                                        'name': 'hss-'+environment+'-orm',
                                        'key': 'LOGGING'
                                    }
                                }
                            },
                            {
                                'name': 'SENDER_EMAIL_ADDRESS',
                                'valueFrom': {
                                    'configMapKeyRef': {
                                        'name': 'hss-'+environment+'-pinpoint',
                                        'key': 'SENDER_EMAIL_ADDRESS'
                                    }
                                }
                            },
                            {
                                'name': 'REPLY_EMAIL_ADDRESS',
                                'valueFrom': {
                                    'configMapKeyRef': {
                                        'name': 'hss-'+environment+'-pinpoint',
                                        'key': 'REPLY_EMAIL_ADDRESS'
                                    }
                                }
                            },
                            {
                                'name': ' APP_ID',
                                'valueFrom': {
                                    'configMapKeyRef': {
                                        'name': 'hss-'+environment+'-pinpoint',
                                        'key': ' APP_ID'
                                    }
                                }
                            },
                            {
                                'name': 'Preferred_Authentication_Method',
                                'valueFrom': {
                                    'configMapKeyRef': {
                                        'name': 'hss-'+environment+'-pinpoint',
                                        'key': 'Preferred_Authentication_Method'
                                    }
                                }
                            },
                            {
                                'name': 'CHANNEL_TYPE',
                                'valueFrom': {
                                    'configMapKeyRef': {
                                        'name': 'hss-'+environment+'-pinpoint',
                                        'key': 'CHANNEL_TYPE'
                                    }
                                }
                            }
                        ]
                    }
                ]
            }
        }
    }

    v1.create_namespaced_deployment(namespace='dfd', body=payload)
    return

def create_authentication_deployment(apiclient, imagetag, repositoryname, imagebase=imagebase):
    v1 = client.AppsV1Api(apiclient)
    deploymentname = repositoryname+'-deployment'

    payload = dict()
    payload['apiVersion'] = "apps/v1"
    payload['kind'] = "Deployment"
    payload['metadata'] = {
        "name": deploymentname,
		"namespace": "dfd"
    }
    payload['spec'] = {
        'replicas' : 1,
        'selector': {
            'matchLabels': {
                'app': repositoryname
            }
        },
        'strategy': {
            'rollingUpdate': {
                'maxSurge': 4,
                'maxUnavailable': 0
            }
        },
        'template': {
            'metadata': {
                'labels': {
                    'app': repositoryname,
                    'project': 'digital-front-door',
                    'release': '1.0'
                }
            },
            'spec': {
                'containers': [
                    {
                        'image': imagebase+repositoryname+':'+imagetag,
                        'name': repositoryname,
                        'command': [
                            'npm',
                            'start'
                        ],
                        'imagePullPolicy': 'Always',
                        'ports': [
                            {
                                'containerPort': 8080
                            }
                        ],
                        'resources': {
                            'limits': {
                                'cpu': '2000m',
                                'memory': '1024Mi'
                            },
                            'requests': {
                                'cpu': '100m',
                                'memory': '128Mi'
                            }
                        }
                    }
                ]
            }
        }
    }

    v1.create_namespaced_deployment(namespace='dfd', body=payload)
    return

def create_scheduler_deployment(apiclient, imagetag, repositoryname, imagebase=imagebase):
    v1 = client.AppsV1Api(apiclient)
    deploymentname = repositoryname+'-deployment'

    payload = dict()
    payload['apiVersion'] = "apps/v1"
    payload['kind'] = "Deployment"
    payload['metadata'] = {
        "name": deploymentname,
		"namespace": "dfd"
    }
    payload['spec'] = {
        'replicas' : 1,
        'selector': {
            'matchLabels': {
                'app': repositoryname
            }
        },
        'strategy': {
            'rollingUpdate': {
                'maxSurge': 4,
                'maxUnavailable': 0
            }
        },
        'template': {
            'metadata': {
                'labels': {
                    'app': repositoryname,
                    'project': 'digital-front-door',
                    'release': '1.0'
                }
            },
            'spec': {
                'containers': [
                    {
                        'image': imagebase+repositoryname+':'+imagetag,
                        'name': repositoryname,
                        'command': [
                            'npm',
                            'start'
                        ],
                        'imagePullPolicy': 'Always',
                        'ports': [
                            {
                                'containerPort': 8080
                            }
                        ],
                        'resources': {
                            'limits': {
                                'cpu': '2000m',
                                'memory': '1024Mi'
                            },
                            'requests': {
                                'cpu': '100m',
                                'memory': '128Mi'
                            }
                        },
                        'env': [
                            {
                                'name': 'RDS_USERNAME',
                                'valueFrom': {
                                    'secretKeyRef': {
                                        'name': 'hss-'+environment+'-db',
                                        'key': 'RDS_USERNAME'
                                    }
                                }
                            },
                            {
                                'name': 'RDS_PASSWORD',
                                'valueFrom': {
                                    'secretKeyRef': {
                                        'name': 'hss-'+environment+'-db',
                                        'key': 'RDS_PASSWORD'
                                    }
                                }
                            },
                            {
                                'name': 'RDS_HOST',
                                'valueFrom': {
                                    'secretKeyRef': {
                                        'name': 'hss-'+environment+'-db',
                                        'key': 'RDS_HOST'
                                    }
                                }
                            },
                            {
                                'name': 'RDS_DATABASE',
                                'valueFrom': {
                                    'configMapKeyRef': {
                                        'name': 'hss-'+environment+'-scheduler',
                                        'key': 'RDS_DATABASE'
                                    }
                                }
                            },
                            {
                                'name': 'FORCE',
                                'valueFrom': {
                                    'configMapKeyRef': {
                                        'name': 'hss-'+environment+'-orm',
                                        'key': 'FORCE'
                                    }
                                }
                            },
                            {
                                'name': 'ALTER',
                                'valueFrom': {
                                    'configMapKeyRef': {
                                        'name': 'hss-'+environment+'-orm',
                                        'key': 'ALTER'
                                    }
                                }
                            },
                            {
                                'name': 'LOGGING',
                                'valueFrom': {
                                    'configMapKeyRef': {
                                        'name': 'hss-'+environment+'-orm',
                                        'key': 'LOGGING'
                                    }
                                }
                            },
                            {
                                'name': 'MESSAGE_SERVICE_URL',
                                'valueFrom': {
                                    'configMapKeyRef': {
                                        'name': 'hss-'+environment+'-scheduler',
                                        'key': 'MESSAGE_SERVICE_URL'
                                    }
                                }
                            },
                            {
                                'name': 'MESSAGE_SERVICE_PORT',
                                'valueFrom': {
                                    'configMapKeyRef': {
                                        'name': 'hss-'+environment+'-scheduler',
                                        'key': 'MESSAGE_SERVICE_PORT'
                                    }
                                }
                            },
                            {
                                'name': 'MESSAGE_NOTIFICATION_SERVICE_PATH',
                                'valueFrom': {
                                    'configMapKeyRef': {
                                        'name': 'hss-'+environment+'-scheduler',
                                        'key': 'MESSAGE_NOTIFICATION_SERVICE_PATH'
                                    }
                                }
                            },
                            {
                                'name': 'MESSAGE_EMAIL_SERVICE_PATH',
                                'valueFrom': {
                                    'configMapKeyRef': {
                                        'name': 'hss-'+environment+'-scheduler',
                                        'key': 'MESSAGE_EMAIL_SERVICE_PATH'
                                    }
                                }
                            },
                            {
                                'name': 'MESSAGE_SERVICE_METHOD',
                                'valueFrom': {
                                    'configMapKeyRef': {
                                        'name': 'hss-'+environment+'-scheduler',
                                        'key': 'MESSAGE_SERVICE_METHOD'
                                    }
                                }
                            }
                        ]
                    }
                ]
            }
        }
    }

    v1.create_namespaced_deployment(namespace='dfd', body=payload)
    return

def create_mvc_deployment(apiclient, imagetag, repositoryname, imagebase=imagebase):
    v1 = client.AppsV1Api(apiclient)
    deploymentname = repositoryname+'-deployment'

    payload = dict()
    payload['apiVersion'] = "apps/v1"
    payload['kind'] = "Deployment"
    payload['metadata'] = {
        "name": deploymentname,
		"namespace": "dfd"
    }
    payload['spec'] = {
        'replicas' : 1,
        'selector': {
            'matchLabels': {
                'app': repositoryname
            }
        },
        'strategy': {
            'rollingUpdate': {
                'maxSurge': 4,
                'maxUnavailable': 0
            }
        },
        'template': {
            'metadata': {
                'labels': {
                    'app': repositoryname,
                    'project': 'digital-front-door',
                    'release': '1.0'
                }
            },
            'spec': {
                'containers': [
                    {
                        'image': imagebase+repositoryname+':'+imagetag,
                        'name': repositoryname,
                        'command': [
                            'npm',
                            'start'
                        ],
                        'imagePullPolicy': 'Always',
                        'ports': [
                            {
                                'containerPort': 8080
                            }
                        ],
                        'resources': {
                            'limits': {
                                'cpu': '2000m',
                                'memory': '1024Mi'
                            },
                            'requests': {
                                'cpu': '100m',
                                'memory': '128Mi'
                            }
                        },
                        'env': [
                            {
                                'name': 'RDS_USERNAME',
                                'valueFrom': {
                                    'secretKeyRef': {
                                        'name': 'hss-'+environment+'-db',
                                        'key': 'RDS_USERNAME'
                                    }
                                }
                            },
                            {
                                'name': 'RDS_PASSWORD',
                                'valueFrom': {
                                    'secretKeyRef': {
                                        'name': 'hss-'+environment+'-db',
                                        'key': 'RDS_PASSWORD'
                                    }
                                }
                            },
                            {
                                'name': 'RDS_HOST',
                                'valueFrom': {
                                    'secretKeyRef': {
                                        'name': 'hss-'+environment+'-db',
                                        'key': 'RDS_HOST'
                                    }
                                }
                            },
                            {
                                'name': 'RDS_DATABASE',
                                'valueFrom': {
                                    'configMapKeyRef': {
                                        'name': 'hss-'+environment+'-scheduler',
                                        'key': 'RDS_DATABASE'
                                    }
                                }
                            },
                            {
                                'name': 'FORCE',
                                'valueFrom': {
                                    'configMapKeyRef': {
                                        'name': 'hss-'+environment+'-orm',
                                        'key': 'FORCE'
                                    }
                                }
                            },
                            {
                                'name': 'ALTER',
                                'valueFrom': {
                                    'configMapKeyRef': {
                                        'name': 'hss-'+environment+'-orm',
                                        'key': 'ALTER'
                                    }
                                }
                            },
                            {
                                'name': 'LOGGING',
                                'valueFrom': {
                                    'configMapKeyRef': {
                                        'name': 'hss-'+environment+'-orm',
                                        'key': 'LOGGING'
                                    }
                                }
                            }
                        ]
                    }
                ]
            }
        }
    }

    v1.create_namespaced_deployment(namespace='dfd', body=payload)
    return

def create_cron_job(apiclient, imagetag, repositoryname, imagebase=imagebase):
    v1 = client.BatchV1beta1Api(apiclient)

    payload = dict()
    payload['apiVersion'] = "batch/v1beta1"
    payload['kind'] = "CronJob"
    payload['metadata'] = {
        "name": repositoryname
    }
    payload['spec'] = {
        'schedule' : '*/5 * * * *',
        'jobTemplate': {
            'spec': {
                'backoffLimit': 2,
                'activeDeadlineSeconds': 900,
                'ttlSecondsAfterFinished': 100,
                'template': {
                    'spec': {
                        'containers': [
                            {
                                'image': imagebase+repositoryname+':'+imagetag,
                                'name': repositoryname,
                                'command': [
                                    'npm',
                                    'start'
                                ],
                                'imagePullPolicy': 'Always',
                                'ports': [
                                    {
                                        'containerPort': 8080
                                    }
                                ],
                                'resources': {
                                    'limits': {
                                        'cpu': '2000m',
                                        'memory': '1024Mi'
                                    },
                                    'requests': {
                                        'cpu': '100m',
                                        'memory': '128Mi'
                                    }
                                },
                                'env': [
                                    {
                                        'name': 'RDS_USERNAME',
                                        'valueFrom': {
                                            'secretKeyRef': {
                                                'name': 'hss-'+environment+'-db',
                                                'key': 'RDS_USERNAME'
                                            }
                                        }
                                    },
                                    {
                                        'name': 'RDS_PASSWORD',
                                        'valueFrom': {
                                            'secretKeyRef': {
                                                'name': 'hss-'+environment+'-db',
                                                'key': 'RDS_PASSWORD'
                                            }
                                        }
                                    },
                                    {
                                        'name': 'RDS_HOST',
                                        'valueFrom': {
                                            'secretKeyRef': {
                                                'name': 'hss-'+environment+'-db',
                                                'key': 'RDS_HOST'
                                            }
                                        }
                                    },
                                    {
                                        'name': 'RDS_DATABASE',
                                        'valueFrom': {
                                            'secretKeyRef': {
                                                'name': 'hss-'+environment+'-db',
                                                'key': 'RDS_DATABASE'
                                            }
                                        }
                                    },
                                    {
                                        'name': 'FORCE',
                                        'valueFrom': {
                                            'configMapKeyRef': {
                                                'name': 'hss-'+environment+'-orm',
                                                'key': 'FORCE'
                                            }
                                        }
                                    },
                                    {
                                        'name': 'ALTER',
                                        'valueFrom': {
                                            'configMapKeyRef': {
                                                'name': 'hss-'+environment+'-orm',
                                                'key': 'ALTER'
                                            }
                                        }
                                    },
                                    {
                                        'name': 'LOGGING',
                                        'valueFrom': {
                                            'configMapKeyRef': {
                                                'name': 'hss-'+environment+'-orm',
                                                'key': 'LOGGING'
                                            }
                                        }
                                    },
                                    {
                                        'name': 'SENDER_EMAIL_ADDRESS',
                                        'valueFrom': {
                                            'configMapKeyRef': {
                                                'name': 'hss-'+environment+'-pinpoint',
                                                'key': 'SENDER_EMAIL_ADDRESS'
                                            }
                                        }
                                    },
                                    {
                                        'name': 'REPLY_EMAIL_ADDRESS',
                                        'valueFrom': {
                                            'configMapKeyRef': {
                                                'name': 'hss-'+environment+'-pinpoint',
                                                'key': 'REPLY_EMAIL_ADDRESS'
                                            }
                                        }
                                    },
                                    {
                                        'name': ' APP_ID',
                                        'valueFrom': {
                                            'configMapKeyRef': {
                                                'name': 'hss-'+environment+'-pinpoint',
                                                'key': ' APP_ID'
                                            }
                                        }
                                    },
                                    {
                                        'name': 'Preferred_Authentication_Method',
                                        'valueFrom': {
                                            'configMapKeyRef': {
                                                'name': 'hss-'+environment+'-pinpoint',
                                                'key': 'Preferred_Authentication_Method'
                                            }
                                        }
                                    },
                                    {
                                        'name': 'CHANNEL_TYPE',
                                        'valueFrom': {
                                            'configMapKeyRef': {
                                                'name': 'hss-'+environment+'-pinpoint',
                                                'key': 'CHANNEL_TYPE'
                                            }
                                        }
                                    }
                                ]
                            }
                        ],
                        'restartPolicy': 'Never'
                    }
                }
            }
        }
    }

    v1.create_namespaced_cron_job(namespace='dfd', body=payload)
    return

def update_deployment(apiclient, imagetag, repositoryname, imagebase=imagebase):
    v1 = client.AppsV1Api(apiclient)
    deploymentname = repositoryname+'-deployment'

    payload = {
	"spec": { 
		"template": {
			"spec": {
				"containers": [
                    {
                        "image": imagebase+repositoryname+':'+imagetag,
                        "name": repositoryname
                    }
                ]
			}
		}
	}
    }

    v1.patch_namespaced_deployment(name=deploymentname, namespace='dfd', body=payload)
    return

def update_cron(apiclient, imagetag, repositoryname, imagebase=imagebase):
    v1 = client.BatchV1beta1Api(apiclient)

    payload = {
	"spec": { 
		"jobTemplate": {
			"spec": {
                "template": {
                    "containers": [
                        {
                            "image": imagebase+repositoryname+':'+imagetag,
                            "name": repositoryname
                        }
                    ]
                }		
			}
		}
	}
    }

    v1.patch_namespaced_cron_job(name=repositoryname, namespace='dfd', body=payload)
    return


def lambda_handler(event, context):
    imagetag = event['detail']['image-tag']
    repositoryname = event['detail']['repository-name']
    token = get_bearer_token()
    apiclient = create_api_client(token)
    if not os.path.exists('tmp/kubeconfig'):
        createkubeconfig()
    try:
        if repositoryname == 'dfd-'+environment+'-node-cronjob-reminders':
            print("Getting cron details...")
            get_cron_job(apiclient, repositoryname)
            print("Cron job found. Proceeding with update...")
            update_cron(apiclient, imagetag, repositoryname)
            print("Update complete.")
        else:
            print("Getting deployment details...")
            get_deployment(apiclient, repositoryname)
            print("Deployment found. Proceeding with deployment update...")
            update_deployment(apiclient, imagetag, repositoryname)
            print("Update complete.")
    except:
        print("Resource does not exist. Creating resource...")
        if repositoryname == 'dfd-'+environment+'-node-user-activity':
            create_user_activity_deployment(apiclient, imagetag, repositoryname)
        elif repositoryname == 'dfd-'+environment+'-node-messaging':
            create_messaging_deployment(apiclient, imagetag, repositoryname)
        elif repositoryname == 'dfd-'+environment+'-node-user-survey':
            create_user_survey_deployment(apiclient, imagetag, repositoryname)
        elif repositoryname == 'dfd-'+environment+'-node-cronjob-reminders':
            create_cron_job(apiclient, imagetag, repositoryname)
        elif repositoryname == 'dfd-'+environment+'-node-authentication':
            create_authentication_deployment(apiclient, imagetag, repositoryname)
        elif repositoryname == 'dfd-'+environment+'-scheduler':
            create_scheduler_deployment(apiclient, imagetag, repositoryname)
        elif repositoryname == 'dfd-'+environment+'-mvc':
            create_mvc_deployment(apiclient, imagetag, repositoryname)
        print("Resource created.")
    return {'Status': 'Success'}