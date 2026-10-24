variable "cluster_name" {
  type    = string
  default = "dispatch"
}

variable "worker_count" {
  type    = number
  default = 3
}

variable "node_port" {
  type    = number
  default = 30080
}

variable "host_port" {
  description = "Host port for manual curl (this project's range is 18100-18199)."
  type        = number
  default     = 18100
}

variable "api_image_repository" {
  type    = string
  default = "dispatch-api"
}

variable "migrate_image_repository" {
  type    = string
  default = "dispatch-migrate"
}

variable "image_tag" {
  description = "Short SHA of the pinned vendor/dispatch-api commit (scripts/build-images.sh prints it)."
  type        = string
}

variable "dispatch_values" {
  type    = any
  default = {}
}
