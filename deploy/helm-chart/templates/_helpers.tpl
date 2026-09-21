{{/*
Expand the name of the chart.
*/}}
{{- define "synapse-memory.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Create a default fully qualified app name.
*/}}
{{- define "synapse-memory.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name := default .Chart.Name .Values.nameOverride }}
{{- if contains $name .Release.Name }}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}
{{- end }}

{{/*
Common labels
*/}}
{{- define "synapse-memory.labels" -}}
helm.sh/chart: {{ include "synapse-memory.name" . }}-{{ .Chart.Version | replace "+" "_" }}
{{ include "synapse-memory.selectorLabels" . }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{/*
Selector labels
*/}}
{{- define "synapse-memory.selectorLabels" -}}
app.kubernetes.io/name: {{ include "synapse-memory.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}
