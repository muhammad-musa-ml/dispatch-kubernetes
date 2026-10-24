terraform {
  required_version = ">= 1.6"
  required_providers {
    kind = {
      source  = "tehcyx/kind"
      version = "~> 0.13"
    }
    helm = {
      source  = "hashicorp/helm"
      version = "~> 3.1"
    }
    kubernetes = {
      source  = "hashicorp/kubernetes"
      version = "~> 3.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.7"
    }
  }
}

locals {
  api_image     = "${var.api_image_repository}:${var.image_tag}"
  migrate_image = "${var.migrate_image_repository}:${var.image_tag}"
}

module "cluster" {
  source = "../../modules/cluster-kind"

  name            = var.cluster_name
  worker_count    = var.worker_count
  node_port       = var.node_port
  host_port       = var.host_port
  kubeconfig_path = abspath("${path.module}/kubeconfig")
  preload_images  = [local.api_image, local.migrate_image]
}

# Providers read the kubeconfig the cluster module wrote. Building them from the kind provider's endpoint
# and certificate attributes instead leaves them empty during `terraform destroy`'s refresh.
provider "kubernetes" {
  config_path = module.cluster.kubeconfig_path
}

provider "helm" {
  kubernetes = {
    config_path = module.cluster.kubeconfig_path
  }
}

module "platform" {
  source = "../../modules/platform"

  charts_path       = abspath("${path.module}/../../../charts")
  api_image         = { repository = var.api_image_repository, tag = var.image_tag }
  migrate_image     = { repository = var.migrate_image_repository, tag = var.image_tag }
  image_pull_policy = "IfNotPresent"
  service_type      = "NodePort"
  node_port         = var.node_port

  database_node_selector = { "dispatch-kubernetes/role" = "data" }
  dispatch_values = merge({
    # The API and the receiver run on the app nodes only, so the experiments can drain one of them.
    nodeSelector = { "dispatch-kubernetes/role" = "app" }
  }, var.dispatch_values)

  depends_on = [module.cluster]
}
