variable "name" { type = string }
variable "size" { type = number }
variable "volume_type" {
  type    = string
  default = null
}
variable "availability_zone" {
  type    = string
  default = null
}
variable "description" {
  type    = string
  default = null
}
variable "metadata" {
  type    = map(string)
  default = {}
}
variable "instance_id" {
  type    = string
  default = null
}
variable "device" {
  type    = string
  default = null
}
