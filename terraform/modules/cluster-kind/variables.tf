variable "name" {
  description = "kind cluster name; node containers are named <name>-control-plane, <name>-worker, <name>-worker2, ..."
  type        = string
  default     = "dispatch"
}

variable "node_image" {
  description = "kindest/node image (pinned by digest)."
  type        = string
  default     = "kindest/node:v1.37.0@sha256:a1ed56cfb0e7b93589bdf97c8cd566405a265939e3620fc4f5de89adff580ae5"
}

variable "worker_count" {
  description = "Number of worker nodes. Worker 1 is labelled as the data node."
  type        = number
  default     = 3
}

variable "data_node_label" {
  description = "Label (key and value) put on the first worker, where PostgreSQL is scheduled."
  type        = object({ key = string, value = string })
  default     = { key = "dispatch-kubernetes/role", value = "data" }
}

variable "app_node_label" {
  description = "Label (key and value) put on every other worker, where the API and the receiver are scheduled."
  type        = object({ key = string, value = string })
  default     = { key = "dispatch-kubernetes/role", value = "app" }
}

variable "node_port" {
  description = "NodePort of the Dispatch Service, published on the control-plane node."
  type        = number
  default     = 30080
}

variable "host_port" {
  description = "Host port mapped to node_port (within the 18100-18199 range reserved for this project)."
  type        = number
  default     = 18100
}

variable "kubeconfig_path" {
  description = "Where the cluster's kubeconfig is written."
  type        = string
}

variable "preload_images" {
  description = "Local Docker images copied into every node with `kind load` after the cluster is created."
  type        = list(string)
  default     = []
}
