output "id" {
  description = "Project ID."
  value       = openstack_identity_project_v3.this.id
}

output "name" {
  description = "Project name."
  value       = openstack_identity_project_v3.this.name
}

output "application_credentials" {
  description = "Application credential IDs and secrets."
  sensitive   = true
  value = {
    for name, credential in openstack_identity_application_credential_v3.this : name => {
      id     = credential.id
      secret = credential.secret
    }
  }
}
