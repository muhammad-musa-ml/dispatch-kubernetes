# The cluster interface the platform layer consumes: the path of a kubeconfig that reaches the cluster.
# A cloud cluster module returns the same output (for example a kubeconfig written from the provider's
# cluster endpoint), so the env that wires it to the platform module changes in one place.
output "kubeconfig_path" {
  value = kind_cluster.this.kubeconfig_path
}
