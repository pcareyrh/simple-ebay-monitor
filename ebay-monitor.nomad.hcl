job "ebay-monitor" {
  datacenters = ["dc1"]
  type        = "service"
  node_pool   = "nfs-nodes"

  group "ebay-monitor" {
    count = 1

    network {
      port "http" {
        static = 8000
      }
    }

    volume "ebay-monitor-data" {
      type            = "csi"
      source          = "ebaymonitordata"
      read_only       = false
      attachment_mode = "file-system"
      access_mode     = "multi-node-multi-writer"
    }

    service {
      name = "ebay-monitor"
      port = "http"

      check {
        type     = "tcp"
        interval = "30s"
        timeout  = "10s"
      }

      meta {
        nomad_ingress_enabled  = true
        nomad_ingress_hostname = "ebay-monitor.service.consul"
      }
    }

    task "ebay-monitor-task" {
      driver = "docker"

      config {
        image      = "ghcr.io/pcareyrh/simple-ebay-monitor:latest"
        ports      = ["http"]
        force_pull = true
      }

      env {
        EBAY_CLIENT_ID     = "your_client_id_here"
        EBAY_CLIENT_SECRET = "your_client_secret_here"
        EBAY_SANDBOX       = "false"
        DATABASE_URL       = "sqlite:////data/ebay_monitor.db"
      }

      volume_mount {
        volume      = "ebay-monitor-data"
        destination = "/data"
        read_only   = false
      }

      resources {
        cpu    = 256 # MHz
        memory = 256 # MB
      }
    }
  }
}
