#!/bin/bash

# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
 
#configure apache
echo "# added by ImageBuilder
<Directory \"/var/www/html\">
  AllowOverride All
  <IfModule mod_rewrite.c>
    RewriteEngine On
    RewriteBase /
    RewriteRule ^wp-admin/includes/ - [F,L]
    RewriteRule !^wp-includes/ - [S=3]
    RewriteRule ^wp-includes/[^/]+\.php$ - [F,L]
    RewriteRule ^wp-includes/js/tinymce/langs/.+\.php - [F,L]
    RewriteRule ^wp-includes/theme-compat/ - [F,L]
  </IfModule>
  <files wp-config.php>
    order allow,deny
    deny from all
  </files>
</Directory>
# deny access to login and admin on this host...
<location /wp-admin>
deny from all
</location>
<location /wp-login.php>
deny from all
</location>
<Directory \"/var/www/html/wp-admin\">
  AllowOverride AuthConfig
</Directory>
# Directory on the disk to contain cached files 
CacheRoot \"/var/cache/httpd/proxy\"
# Cache all
CacheEnable disk \"/\"
# Enable cache and set 15-minute caching as the default
ExpiresActive On
ExpiresDefault \"access plus 15 minutes\"
# Force no caching for PHP files
<FilesMatch \"\.(php)$\">
    ExpiresActive Off
</FilesMatch>" > /etc/httpd/conf.d/wordpress.conf

apachectl restart
systemctl restart php-fpm
