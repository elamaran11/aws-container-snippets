aws eks --region us-east-1 update-kubeconfig --name <cluster-name>
kubectl config get-contexts --kubeconfig=/root/.kube/config

kubectl get jobs -n <namespace> -o go-template --template '{{range .items}}{{.metadata.name}} {{.metadata.creationTimestamp}}{{"\n"}}{{end}}' --kubeconfig=/root/.kube/config | awk '$2 <= "'$(date -d'now-6 hours' -Ins --utc | sed 's/+0000/Z/')'" { print $1 }' | xargs --no-run-if-empty kubectl delete job -n <namespace> --kubeconfig=/root/.kube/config
