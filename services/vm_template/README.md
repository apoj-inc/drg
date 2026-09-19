# Shared cloud-init VM templates

This Terraform root creates the shared Debian 13 and Fedora 44 GenericCloud
cloud-init templates. Apply it before any service creates a linked clone.

The Fedora template is intended for native FreeIPA VMs and is kept separate
from the Debian template so existing service clones are unaffected.

The source image is deliberately required as a pinned URL and an exact filename:

```hcl
debian_cloud_image_url      = "https://cloud.debian.org/images/cloud/trixie/<release>/debian-13-genericcloud-amd64-<release>.qcow2"
debian_cloud_image_filename = "debian-13-genericcloud-amd64-<release>.qcow2"
debian_cloud_template_vm_id = 9000
```

The selected `disk_datastore_id` must support linked clones. Service roots reference `debian_cloud_template_vm_id`; they do not create their own template.

The Fedora image is pinned by default to the Fedora 44 GenericCloud QEMU image:

```hcl
fedora_cloud_image_url      = "https://download.fedoraproject.org/pub/fedora/linux/releases/44/Cloud/x86_64/images/Fedora-Cloud-Base-Generic-44-1.7.x86_64.qcow2"
fedora_cloud_image_filename = "Fedora-Cloud-Base-Generic-44-1.7.x86_64.qcow2"
fedora_cloud_template_vm_id = 9001
```

Verify the Fedora image against Fedora's signed checksum before applying the
template. The official Fedora Cloud download page publishes the checksum and
verification instructions.
