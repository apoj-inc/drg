variable "name" { type = string }
variable "image_source_url" { type = string }
variable "image_source_checksum" { type = string }
variable "image_source_checksum_algo" {
  type    = string
  default = "sha256"
}
variable "disk_format" {
  type    = string
  default = "qcow2"
}
variable "container_format" {
  type    = string
  default = "bare"
}
variable "visibility" {
  type    = string
  default = "private"
}
variable "min_disk_gb" {
  type    = number
  default = 0
}
variable "min_ram_mb" {
  type    = number
  default = 0
}
variable "properties" {
  type    = map(string)
  default = {}
}
