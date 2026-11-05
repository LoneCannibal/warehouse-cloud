{{/*
Settings shared by every Application. The finalizer makes deleting an Application
delete what it deployed, which `make down` relies on to remove the ALB and EBS volumes.
*/}}
{{- define "warehouse.metadata" -}}
namespace: argocd
finalizers:
  - resources-finalizer.argocd.argoproj.io
{{- end }}

{{- define "warehouse.syncPolicy" -}}
automated:
  prune: true
  selfHeal: true
retry:
  limit: 10
  backoff:
    duration: 15s
    factor: 2
    maxDuration: 3m
{{- end }}
