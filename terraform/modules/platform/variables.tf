variable "namespace" {
  type    = string
  default = "dispatch"
}

variable "charts_path" {
  description = "Directory that holds the dispatch and dispatch-postgres charts."
  type        = string
}

variable "api_image" {
  description = "Dispatch API (and demo receiver) image, repository and tag."
  type        = object({ repository = string, tag = string })
}

variable "migrate_image" {
  description = "Migration runner image, repository and tag."
  type        = object({ repository = string, tag = string })
}

variable "image_pull_policy" {
  description = "IfNotPresent for images preloaded into kind; Always or IfNotPresent for a registry."
  type        = string
  default     = "IfNotPresent"
}

variable "service_type" {
  description = "Service type of the Dispatch API: NodePort on kind, LoadBalancer or ClusterIP behind an ingress elsewhere."
  type        = string
  default     = "ClusterIP"
}

variable "node_port" {
  type    = number
  default = null
}

variable "database_node_selector" {
  description = "Node selector for the PostgreSQL pod. Empty when the storage class is not node-local."
  type        = map(string)
  default     = {}
}

variable "pool_max_conns" {
  description = "Connections each Dispatch replica may open. Without this, pgx sizes the pool as max(4, CPUs visible to the process): 24 on the machine in results/pool-default.txt, so 8 replicas could ask for 192 of PostgreSQL's 200 connections."
  type        = number
  default     = 10
}

variable "metrics_server_chart_version" {
  type    = string
  default = "3.14.0"
}

variable "dispatch_values" {
  description = "Extra values merged over the chart defaults, as a map (for example autoscaling or resources)."
  type        = any
  default     = {}
}
