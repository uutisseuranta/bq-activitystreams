# terraform/networking.tf
# VPC-verkko ja Serverless VPC Access Connector.
#
# NYKYTILA: EI KÄYTÖSSÄ – tiedosto dokumentoi päätöksen.
#
# Miksi VPC ei tarvita tässä projektissa:
#
#   1. Cloud Run -palvelut kommunikoivat BigQueryn kanssa Google Cloud
#      sisäisen verkon (Private Google Access) kautta pelkällä IAM-
#      autentikoinnilla – VPC-tunneli ei tuo lisäturvaa tähän yhteyteen.
#
#   2. Cloud SQL, Memorystore (Redis) tai muita VPC-sisäisiä resursseja
#      ei ole käytössä. Jos sellaisia lisätään, Serverless VPC Access
#      Connector on lisättävä tähän tiedostoon.
#
#   3. Serverless VPC Access Connector maksaa noin 5–10 €/kk vaikka
#      se olisi idle. Turha kustannus nykytopologialle.
#
# Milloin VPC lisätään:
#
#   [ ] Cloud SQL (PostgreSQL/MySQL) otetaan käyttöön
#   [ ] Memorystore Redis -välimuisti lisätään
#   [ ] Private Service Connect tai Shared VPC vaaditaan
#
# Jos VPC tarvitaan, poista kommentit alla olevista resursseista
# ja ota connector käyttöön cloudrun_services.tf:ssä
# vpc_access { connector = google_vpc_access_connector.serverless.id
#              egress = "PRIVATE_RANGES_ONLY" } -lohkolla.

# resource "google_compute_network" "main" {
#   name                    = "bq-activitystreams-vpc"
#   auto_create_subnetworks = false
# }
#
# resource "google_compute_subnetwork" "main" {
#   name          = "backend-subnet"
#   ip_cidr_range = "10.0.0.0/24"
#   region        = var.region
#   network       = google_compute_network.main.id
# }
#
# resource "google_vpc_access_connector" "serverless" {
#   name          = "backend-connector"
#   region        = var.region
#   subnet {
#     name = google_compute_subnetwork.main.name
#   }
#   machine_type  = "e2-micro"
#   min_instances = 2
#   max_instances = 3
# }
