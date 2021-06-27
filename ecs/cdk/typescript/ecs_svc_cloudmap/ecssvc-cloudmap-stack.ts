import * as ec2 from '@aws-cdk/aws-ec2';
import * as cdk from '@aws-cdk/core';
import * as ecs from '@aws-cdk/aws-ecs';
import * as servicediscovery from '@aws-cdk/aws-servicediscovery';

export class SampleECSStack extends cdk.Stack {
  constructor(scope: cdk.Construct, id: string, props?: cdk.StackProps) {
    super(scope, id, props);

const app = new cdk.App();
const stack = new cdk.Stack(app, 'aws-service-integration');


const vpc = new ec2.Vpc(stack, 'Vpc', { maxAzs: 1 });

const namespace1 = new servicediscovery.PrivateDnsNamespace(stack, 'NamMyNamespace1', {
  name: 'MyNamespace1',
  vpc,
});


const discoveryservice1 = namespace1.createService('IpService1', {
  description: 'service registering non-ip instances',
});


//Create Cluster
const cluster = new ecs.Cluster(stack, 'Cluster1', {
  vpc,
});

// Add capacity to it
cluster.addCapacity('DefaultAutoScalingGroupCapacity', {
  instanceType: new ec2.InstanceType("t2.xlarge"),
  desiredCapacity: 1,
});
const taskDefinition = new ecs.Ec2TaskDefinition(stack, 'TaskDef');

const container = taskDefinition.addContainer('DefaultContainer1', {
  image: ecs.ContainerImage.fromRegistry("amazon/amazon-ecs-sample"),
  memoryLimitMiB: 512,
});

container.addPortMappings({
containerPort: 80,
hostPort: 8080,
protocol: ecs.Protocol.TCP
});

const ecsService1 = new ecs.Ec2Service(stack, 'ecs-Service1', {
  cluster,
  taskDefinition,
});

discoveryservice1.registerIpInstance('IpInstance', {
  ipv4: '10.0.204.129',
  port:8080,
});

  }
}