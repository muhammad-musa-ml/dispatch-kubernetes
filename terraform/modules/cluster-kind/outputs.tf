# The cluster interface the platform layer consumes: the path of a kubeconfig that reaches the cluster.
# A cloud cluster module returns the same output (for example a kubeconfig written from the provider's
# cluster endpoint), so the env that wires it to the platform module changes in one place.
#
# The path itself is an input, so Terraform knows it at plan time, and the Kubernetes and Helm providers
# check that the file exists as soon as they are configured -- before the cluster that writes it. The empty
# slice of the cluster id is unknown until the cluster exists, which makes the whole value unknown on the
# first apply and so configures those providers after the cluster is created. On every later plan and on
# destroy the id comes from the state, and the file is there.
output "kubeconfig_path" {
  value = "${kind_cluster.this.kubeconfig_path}${substr(kind_cluster.this.id, 0, 0)}"
}
