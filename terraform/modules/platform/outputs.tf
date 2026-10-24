output "namespace" {
  value = kubernetes_namespace_v1.this.metadata[0].name
}

output "api_keys" {
  description = "Bearer key per tenant (the harness reads them from the Secret instead)."
  value       = { for t in local.tenants : t => random_password.api_key[t].result }
  sensitive   = true
}
