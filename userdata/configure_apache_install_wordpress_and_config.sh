#!/bin/bash

# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
 
#check if wordpress.conf exists, and if not create it
if [ ! -f /etc/httpd/conf.d/wordpress.conf ]
then
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
fi
if [ ! -f {efs_mount_dir}/index.php ]
then
  echo "Downloading & installing wordpress..."
  cd {efs_mount_dir}
  wget http://wordpress.org/latest.tar.gz
  tar -xzf latest.tar.gz
  rm -f latest.tar.gz
  mv wordpress/* {efs_mount_dir}
  rm -r wordpress
  echo "Setting permissions as per https://wordpress.org/support/article/hardening-wordpress/"
  chown -R apache:apache {efs_mount_dir}
  chmod 2755 {efs_mount_dir} && find {efs_mount_dir} -type d -exec chmod 2755 {{}} \;
  find {efs_mount_dir} -type f -exec chmod 0644 {{}} \;
fi
#check if wp-config.php exists, and if it does, do not re-install WordPress
if [ ! -f {efs_mount_dir}/wp-config.php ]
then
  touch {efs_mount_dir}/wp-config.php
  SECRET=$({db_secret_command})
  USERNAME=`echo $SECRET | jq -r '.username'`
  PASSWORD=`echo $SECRET | jq -r '.password'`
  DBNAME=`echo $SECRET | jq -r '.dbname'`
  HOST=`echo $SECRET | jq -r '.host'`
  KEYS_AND_SALTS=`curl https://api.wordpress.org/secret-key/1.1/salt/`
  echo "<?php
\$_SERVER['HTTPS']='on';
define('DB_NAME', '$DBNAME');
define('DB_USER', '$USERNAME');
define('DB_PASSWORD', '$PASSWORD');
define('DB_HOST', '$HOST');
define('WP_HOME', 'https://{site_hostname}');
define('WP_SITEURL', 'https://{site_hostname}');
define('DB_CHARSET', 'utf8');
define('DB_COLLATE', '');
$KEYS_AND_SALTS
\$table_prefix = 'wp_core_';
define('FS_METHOD', 'direct');
define('WP_DEBUG', false );
define('FORCE_SSL_ADMIN', true);
define('DISALLOW_FILE_EDIT', true);
if ( ! defined('ABSPATH') ) {{
define('ABSPATH', __DIR__ . '/');
}}
require_once ABSPATH . 'wp-settings.php';" > {efs_mount_dir}/wp-config.php
  chmod 440 {efs_mount_dir}/wp-config.php
  rm -f {efs_mount_dir}/wp-config-sample.php
fi
apachectl restart
systemctl start php-fpm
