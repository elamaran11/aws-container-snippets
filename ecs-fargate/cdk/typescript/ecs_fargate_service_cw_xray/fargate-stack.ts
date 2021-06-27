import * as cdk from '@aws-cdk/core';
import * as ec2 from '@aws-cdk/aws-ec2';
import * as ecs from '@aws-cdk/aws-ecs';
import * as path from 'path';
import * as elbv2 from '@aws-cdk/aws-elasticloadbalancingv2';
// import * as ecs_patterns from "@aws-cdk/aws-ecs-patterns";
import * as iam from '@aws-cdk/aws-iam';
import { ManagedPolicy } from '@aws-cdk/aws-iam';

export class SampleFargateStack extends cdk.Stack {
  constructor(scope: cdk.Construct, id: string, props?: cdk.StackProps) {
    super(scope, id, props);
        // ECS task role
    const ecsTaskRole = new iam.Role(this, `ecs-taskRole-${this.stackName}`, {
        roleName: `ecs-taskRole-${this.stackName}`,
        assumedBy: new iam.ServicePrincipal('ecs-tasks.amazonaws.com')
        });
    ecsTaskRole.addManagedPolicy(ManagedPolicy.fromAwsManagedPolicyName("service-role/AmazonECSTaskExecutionRolePolicy"))
    ecsTaskRole.addManagedPolicy(ManagedPolicy.fromAwsManagedPolicyName("AmazonRDSFullAccess"))
    ecsTaskRole.addManagedPolicy(ManagedPolicy.fromAwsManagedPolicyName("AmazonEC2ContainerRegistryFullAccess"))
    ecsTaskRole.addManagedPolicy(ManagedPolicy.fromAwsManagedPolicyName("CloudWatchLogsFullAccess"))
    ecsTaskRole.addManagedPolicy(ManagedPolicy.fromAwsManagedPolicyName("AWSXrayFullAccess"))
    //Role Policy
    const executionRolePolicy =  new iam.PolicyStatement({
        effect: iam.Effect.ALLOW,
        resources: ['*'],
        actions: [
            "ecr:GetAuthorizationToken",
            "ecr:BatchCheckLayerAvailability",
            "ecr:GetDownloadUrlForLayer",
            "ecr:BatchGetImage",
            "logs:CreateLogStream",
            "logs:PutLogEvents"
            ]
    });

    const MyVpc = new ec2.Vpc(this, "MyVpc", {
      maxAzs: 3
    });

    const MyCluster = new ecs.Cluster(this, "FargateCluster", {
      vpc: MyVpc
    });
    
    // Setup capacity providerscdk synth
    const cfnEcsCluster = MyCluster.node.defaultChild as ecs.CfnCluster;
    cfnEcsCluster.capacityProviders = ['FARGATE'];
    // cfnEcsCluster.capacityProviders = ['FARGATE', 'FARGATE_SPOT'];

    // const PetclinicUiTaskDef = new ecs.FargateTaskDefinition(this, 'TaskDef');
    //Create a task definition with 2 containers and Cloudwatch Logs
    const PetclinicUiTaskDef = new ecs.FargateTaskDefinition(this, 'petclinic-ui-task-def', {
        taskRole: ecsTaskRole,
        memoryLimitMiB: 4096,
        cpu:2048
    });

    PetclinicUiTaskDef.addToExecutionRolePolicy(executionRolePolicy);
     // Add app container
     const appLogging = new ecs.AwsLogDriver({
         streamPrefix: "ecs",
     });

     const XrayLoggingUi = new ecs.AwsLogDriver({
        streamPrefix: "x-ray-demon-logs-ui"
    });


    const PetclinicUiContainer = PetclinicUiTaskDef.addContainer('DefaultContainer', {
      image: ecs.ContainerImage.fromRegistry("amazon/amazon-ecs-sample"),
      memoryLimitMiB: 512,
      logging: appLogging,
      essential: true,
      environment : {
         AWS_XRAY_CONTEXT_MISSING : 'LOG_ERROR',
      }

    });
    
    PetclinicUiContainer.addPortMappings({
         containerPort: 80
    });

    // const PetclinicUiXrayImage = new ecs.AssetImage(path.join(__dirname, '../..', 'xray-demon'))

    const PetclinicUiXrayContainer = PetclinicUiTaskDef.addContainer("xray-daemon-ui",{
        image: ecs.ContainerImage.fromRegistry("amazon/aws-xray-daemon"),
        logging: XrayLoggingUi,
        cpu: 32,
        essential: true,
        memoryLimitMiB: 256
    })
    PetclinicUiXrayContainer.addPortMappings({
         containerPort: 2000,
         hostPort: 2000,
         protocol: ecs.Protocol.UDP,
    })


    // Instantiate an Amazon ECS Service
    const PetclinicUiService = new ecs.FargateService(this, 'Fargate-Service', {
      cluster: MyCluster,
      taskDefinition: PetclinicUiTaskDef,
      desiredCount: 3,
      serviceName: 'Petclinic-Ui-Service',

    });
    
   // Setup autoscaling
    const scalingUi = PetclinicUiService.autoScaleTaskCount({ maxCapacity: 5 });

    scalingUi.scaleOnCpuUtilization('UiCpuScaling', {
        targetUtilizationPercent: 50,
    });

    scalingUi.scaleOnSchedule('ScheduleScalingUp', {
        minCapacity: 3,
        schedule: {
            expressionString: "cron(0 0 0/2 ? * *)"
        },
    })

    scalingUi.scaleOnSchedule('ScheduleScalingDown', {
        minCapacity: 2,
        schedule: {
            expressionString: "cron(0 0 1/2 ? * *)"
        }
    });

    //Security Group   
    const LbSecurityGroup = new ec2.SecurityGroup(this,'petclinic-lb-sg', {
    vpc : MyVpc,
    description: 'Enable inbound rules LB',
    securityGroupName: 'petclinic-lb-sg',
    allowAllOutbound: true,
    });
    LbSecurityGroup.addIngressRule(ec2.Peer.anyIpv4(),ec2.Port.tcp(80),'allow all traffic');

    const securityGroup: ec2.ISecurityGroup = LbSecurityGroup

    // Setup Load balancer & register targets
    const lb = new elbv2.ApplicationLoadBalancer(this, 'petclinic-load-balancer', {
         vpc: MyVpc,
         internetFacing: true,
         securityGroup: securityGroup,
    });
      // Default target routing
    const listener = lb.addListener('Petclinic-Http-Listner', { port: 80 });
    
    listener.addTargets('ui-default-target', {
          targetGroupName: 'default-ui-target-group',
          port: 80,
          healthCheck: {
              enabled: true,
              path: '/actuator/health',
          },
          targets: [PetclinicUiService]
    });
    // CfnOutput the DNS where you can access your service
    new cdk.CfnOutput(this, 'LoadBalancerDNS', { value: lb.loadBalancerDnsName });

    

    // Create a load-balanced Fargate service and make it public
    // new ecs_patterns.ApplicationLoadBalancedFargateService(this, "MyFargateService", {
    //   cluster: cluster, // Required
    //   desiredCount: 3, // Default is 1
    //     taskImageOptions: { image: ecs.ContainerImage.fromRegistry("amazon/amazon-ecs-sample") },
    //     publicLoadBalancer: true // Default is false
    // });
  }
}
