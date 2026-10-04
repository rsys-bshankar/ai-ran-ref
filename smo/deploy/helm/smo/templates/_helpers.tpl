{{/* The tag of every SMO image. */}}
{{- define "smo.tag" -}}
{{- default .Chart.AppVersion .Values.image.tag -}}
{{- end -}}

{{/* The image of a module: smo.image (dict "root" . "name" "sme"). */}}
{{- define "smo.image" -}}
{{- printf "%s/%s%s:%s" .root.Values.image.registry .root.Values.image.prefix .name (include "smo.tag" .root) -}}
{{- end -}}

{{/* The module's settings: the defaults with what the module names laid over them. smo.module (dict "root" . "name" "sme"). */}}
{{- define "smo.module" -}}
{{- $m := deepCopy .root.Values.moduleDefaults -}}
{{- $m = mustMergeOverwrite $m (get .root.Values.modules .name) -}}
{{- toYaml $m -}}
{{- end -}}

{{- define "smo.labels" -}}
app.kubernetes.io/part-of: smo
app.kubernetes.io/managed-by: {{ .root.Release.Service }}
app.kubernetes.io/instance: {{ .root.Release.Name }}
helm.sh/chart: {{ printf "%s-%s" .root.Chart.Name .root.Chart.Version | replace "+" "_" }}
{{- end -}}

{{/* The Secret holding db-password and enrollment-secret. */}}
{{- define "smo.secretName" -}}
{{- default "smo-secrets" .Values.secrets.existingSecret -}}
{{- end -}}

{{/* Where Postgres is, as the URL every module takes (the password is read from the file, never put in the URL). */}}
{{- define "smo.databaseUrl" -}}
{{- if .Values.postgres.enabled -}}
postgresql+psycopg://smo@postgres:5432/smo
{{- else -}}
{{- $e := .Values.postgres.external -}}
{{- if not $e.host }}{{ fail "postgres.enabled=false needs postgres.external.host" }}{{ end -}}
{{- printf "postgresql+psycopg://%s@%s:%v/%s?sslmode=%s" $e.user $e.host $e.port $e.database $e.sslmode -}}
{{- end -}}
{{- end -}}

{{/* The pull secrets of a pod spec. */}}
{{- define "smo.pullSecrets" -}}
{{- with .Values.image.pullSecrets }}
imagePullSecrets:
{{- range . }}
  - name: {{ . }}
{{- end }}
{{- end }}
{{- end -}}

{{/* The environment of a container that talks to the database: the URL, the password file and (when it has one) the enrollment secret file. */}}
{{- define "smo.dbEnv" -}}
- name: SMO_DATABASE_URL
  value: {{ include "smo.databaseUrl" .root | quote }}
- name: SMO_DATABASE_PASSWORD_FILE
  value: /run/secrets/db_password
{{- end -}}
