aws eks --region us-east-1 update-kubeconfig --name narnia-integration-cluster
kubectl config get-contexts --kubeconfig=/root/.kube/config
kubectl get jobs -n ta-ta -o go-template --template '{{range .items}}{{.metadata.name}} {{.metadata.creationTimestamp}}{{"\n"}}{{end}}' --kubeconfig=/root/.kube/config
kubectl get jobs -n ta-ta -o go-template --template '{{range .items}}{{.metadata.name}} {{.metadata.creationTimestamp}}{{"\n"}}{{end}}' --kubeconfig=/root/.kube/config | awk '$2 <= "'$(date -d'now-6 hours' -Ins --utc | sed 's/+0000/Z/')'" { print $1 }' | xargs --no-run-if-empty kubectl delete job -n ta-ta --kubeconfig=/root/.kube/config


kubectl get nodes -o go-template='{{range .items}}{{.metadata.name}}{{"\n"}}{{end}}'| xargs --no-run-if-empty kubectl taint nodes spotInstance=true:PreferNoSchedule

for node in `kubectl get nodes --label-columns=lifecycle --selector=lifecycle=Ec2Spot -o go-template='{{range .items}}{{.metadata.name}}{{"\n"}}{{end}}'` ; do
  kubectl taint nodes $node spotInstance=true:PreferNoSchedule
done

for node in `kubectl get nodes -o go-template='{{range .items}}{{.metadata.name}}{{"\n"}}{{end}}'` ; do
  echo $node
  kubectl patch nodes $node -p '{"spec":{"taints":[]}}'
done

kubectl taint nodes ip-192-168-18-89.us-west-2.compute.internal spotInstance=true:PreferNoSchedule