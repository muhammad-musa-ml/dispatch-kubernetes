resource "kubernetes_namespace_v1" "this" {
  metadata {
    name = var.namespace
  }
}

# --- metrics-server: the HorizontalPodAutoscaler's CPU signal ---------------------------------------
# kind kubelets serve self-signed certificates, hence --kubelet-insecure-tls. A 15 s resolution (the
# default is 60 s) keeps the autoscaler's reaction time measurable.
resource "helm_release" "metrics_server" {
  name       = "metrics-server"
  namespace  = "kube-system"
  repository = "https://kubernetes-sigs.github.io/metrics-server/"
  chart      = "metrics-server"
  version    = var.metrics_server_chart_version
  wait       = true
  timeout    = 300

  values = [yamlencode({
    args = ["--kubelet-insecure-tls", "--metric-resolution=15s"]
  })]
}

# --- Secrets: generated here, referenced by name from the charts -------------------------------------
resource "random_password" "database" {
  length  = 32
  special = false
}

resource "random_password" "api_key" {
  for_each = toset(["alpha", "bravo"])
  length   = 40
  special  = false
}

resource "random_password" "webhook" {
  length  = 48
  special = false
}

locals {
  database_host = "dispatch-postgres.${var.namespace}.svc.cluster.local"
  db_url_base   = "postgres://dispatch:${random_password.database.result}@${local.database_host}:5432/dispatch?sslmode=disable"
  tenants       = ["alpha", "bravo"]
}

resource "kubernetes_secret_v1" "postgres" {
  metadata {
    name      = "dispatch-postgres"
    namespace = kubernetes_namespace_v1.this.metadata[0].name
  }
  data = {
    POSTGRES_PASSWORD = random_password.database.result
  }
}

resource "kubernetes_secret_v1" "app" {
  metadata {
    name      = "dispatch-app"
    namespace = kubernetes_namespace_v1.this.metadata[0].name
  }
  data = {
    DATABASE_URL           = "${local.db_url_base}&pool_max_conns=${var.pool_max_conns}"
    MIGRATION_DATABASE_URL = local.db_url_base
    API_KEYS               = jsonencode({ for t in local.tenants : random_password.api_key[t].result => t })
    DELIVERY_TARGETS = jsonencode({
      for t in local.tenants : t => {
        url    = "http://dispatch-receiver.${var.namespace}.svc.cluster.local:8081/webhook"
        secret = random_password.webhook.result
      }
    })
  }
}

resource "kubernetes_secret_v1" "receiver" {
  metadata {
    name      = "dispatch-receiver"
    namespace = kubernetes_namespace_v1.this.metadata[0].name
  }
  data = {
    WEBHOOK_SECRET = random_password.webhook.result
  }
}

# --- PostgreSQL, then Dispatch (its pre-install migration Job needs the database to exist) -----------
resource "helm_release" "postgres" {
  name      = "dispatch-postgres"
  namespace = kubernetes_namespace_v1.this.metadata[0].name
  chart     = "${var.charts_path}/dispatch-postgres"
  wait      = true
  timeout   = 300

  values = [yamlencode({
    nodeSelector   = var.database_node_selector
    passwordSecret = kubernetes_secret_v1.postgres.metadata[0].name
  })]
}

resource "helm_release" "dispatch" {
  name            = "dispatch"
  namespace       = kubernetes_namespace_v1.this.metadata[0].name
  chart           = "${var.charts_path}/dispatch"
  wait            = true
  timeout         = 300
  cleanup_on_fail = true

  values = [yamlencode(merge({
    image = {
      repository = var.api_image.repository
      tag        = var.api_image.tag
      pullPolicy = var.image_pull_policy
    }
    migrateImage = {
      repository = var.migrate_image.repository
      tag        = var.migrate_image.tag
      pullPolicy = var.image_pull_policy
    }
    secrets = {
      app      = kubernetes_secret_v1.app.metadata[0].name
      receiver = kubernetes_secret_v1.receiver.metadata[0].name
    }
    service = {
      type     = var.service_type
      nodePort = var.node_port
    }
  }, var.dispatch_values))]

  depends_on = [helm_release.postgres, helm_release.metrics_server]
}
