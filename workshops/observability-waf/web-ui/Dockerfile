FROM nginx:alpine

# Copy static HTML files
COPY index.html /usr/share/nginx/html/
COPY nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 9873

CMD ["nginx", "-g", "daemon off;"]
