resource "kind_cluster" "this" {
  name            = var.name
  node_image      = var.node_image
  wait_for_ready  = true
  kubeconfig_path = var.kubeconfig_path

  kind_config {
    kind        = "Cluster"
    api_version = "kind.x-k8s.io/v1alpha4"

    node {
      role = "control-plane"
      extra_port_mappings {
        container_port = var.node_port
        host_port      = var.host_port
        listen_address = "127.0.0.1"
      }
    }

    dynamic "node" {
      for_each = range(var.worker_count)
      content {
        role   = "worker"
        labels = node.key == 0 ? { (var.data_node_label.key) = var.data_node_label.value } : { (var.app_node_label.key) = var.app_node_label.value }
      }
    }
  }
}

# Images built on the host are not visible to the nodes' container runtime; copy them in. This is the
# only kind-specific step and it lives here so the platform module does not know about it.
resource "terraform_data" "preload" {
  count = length(var.preload_images) > 0 ? 1 : 0

  triggers_replace = [kind_cluster.this.id, join(",", var.preload_images)]

  provisioner "local-exec" {
    interpreter = ["bash", "-c"]
    command     = "kind load docker-image ${join(" ", var.preload_images)} --name ${var.name}"
  }
}
