{{- define "dispatch.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "dispatch.fullname" -}}
{{- if .Values.fullnameOverride -}}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- $name := default .Chart.Name .Values.nameOverride -}}
{{- if contains $name .Release.Name -}}
{{- .Release.Name | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{- define "dispatch.labels" -}}
app.kubernetes.io/name: {{ include "dispatch.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version }}
{{- end -}}

{{- define "dispatch.selectorLabels" -}}
app.kubernetes.io/name: {{ include "dispatch.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/component: api
{{- end -}}

{{- define "dispatch.receiverSelectorLabels" -}}
app.kubernetes.io/name: {{ include "dispatch.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/component: receiver
{{- end -}}

{{- define "dispatch.image" -}}
{{- printf "%s:%s" .Values.image.repository (default .Chart.AppVersion .Values.image.tag) -}}
{{- end -}}

{{- define "dispatch.migrateImage" -}}
{{- printf "%s:%s" .Values.migrateImage.repository (default .Chart.AppVersion .Values.migrateImage.tag) -}}
{{- end -}}

{{/* Sum of the termination budget; the parts are documented in values.yaml. */}}
{{- define "dispatch.terminationGracePeriodSeconds" -}}
{{- add .Values.termination.preStopSleep .Values.termination.httpDrain .Values.termination.workerAttempt .Values.termination.margin -}}
{{- end -}}
