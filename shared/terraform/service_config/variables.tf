variable "platform_config_file" {
  description = "Absolute path to the non-secret platform YAML configuration."
  type        = string
}

variable "service_config_file" {
  description = "Absolute path to the non-secret service YAML configuration."
  type        = string
}

variable "cluster" {
  description = "Platform cluster selected for this Terraform deployment unit."
  type        = string
}

variable "contour" {
  description = "Platform rollout contour selected for this Terraform deployment unit."
  type        = string
}
