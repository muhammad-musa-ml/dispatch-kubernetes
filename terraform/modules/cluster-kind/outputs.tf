# The cluster interface the platform module consumes. A cloud cluster module returns the same shape.
output "connection" {
  value = {
    host                   = kind_cluster.this.endpoint
    cluster_ca_certificate = kind_cluster.this.cluster_ca_certificate
    client_certificate     = kind_cluster.this.client_certificate
    client_key             = kind_cluster.this.client_key
  }
  sensitive = true
}

output "kubeconfig_path" {
  value = kind_cluster.this.kubeconfig_path
}

# Needed only by the preload step to order the platform after it.
output "ready" {
  value = length(terraform_data.preload) > 0 ? terraform_data.preload[0].id : kind_cluster.this.id
}
