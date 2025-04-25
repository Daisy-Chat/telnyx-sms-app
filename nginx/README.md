# Nginx Reverse Proxy Setup

Assuming:

- Your app is running on `localhost:8000`
- You have a real domain like `sms.yourdomain.com`

Rename `nginx.example` to `nginx.conf` and edit it with your domain name.

## Information

| Setting | Purpose |
| ------- | ------- |
| `listen 80` | Accept HTTP, redirect to HTTPS |
| `listen 443 ssl` | Accept HTTPS |
| `proxy_pass http://127.0.0.1:8000/` | Forward to your app inside Docker |
| `ssl_certificate` | Your Let's Encrypt cert |
| Security headers | Harden against basic attacks |
