output "kubeconfig_path" {
  value = module.cluster.kubeconfig_path
}

output "namespace" {
  value = module.platform.namespace
}

output "api_keys" {
  value     = module.platform.api_keys
  sensitive = true
}
