# FreeIPA certificate profiles

Dogtag certificate profiles are repository-owned files and are applied by
Ansible with `ipa certprofile-import` when declared here.

Custom profiles must be validated with `ipa certprofile-import --file` on a
non-production FreeIPA instance before being added to this directory.
