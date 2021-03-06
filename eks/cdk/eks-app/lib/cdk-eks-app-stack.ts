import * as cdk from '@aws-cdk/core';
import * as eks from '@aws-cdk/aws-eks';
import * as cdk8s from 'cdk8s';
import { MyChart } from './my-chart';

export class CdkEksAppStack extends cdk.Stack {
  constructor(scope: cdk.Construct, id: string, props?: cdk.StackProps) {
    super(scope, id, props);

    // The code that defines your stack goes here
    const cluster = new eks.Cluster(this, 'cdk-cdk8s-cluster', {
      version: eks.KubernetesVersion.V1_18,
    });

    //    cluster.addFargateProfile('FargateProfile', {
    //      selectors: [
    //        { namespace: 'default'},
    //        { namespace: 'kube-system'}
    //      ]
    //  });    

    // cluster.addCdk8sChart('my-chart', new MyChart(new cdk8s.App(), 'MyChart'));



  }
}