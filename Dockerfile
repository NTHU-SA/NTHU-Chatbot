# local build
# FROM nginx:alpine
# COPY ./dist /usr/share/nginx/html
# COPY ./nginx/nginx.conf /etc/nginx/nginx.conf
# CMD ["nginx", "-g", "daemon off;"]


# # build stage
FROM node:14-alpine as build-stage
WORKDIR /app

RUN apk update && apk add python3 make g++ curl

# # build stage
COPY package.json ./
COPY yarn.lock ./
RUN npm install -g npm@8.4.1
RUN npx browserslist@latest --update-db
RUN yarn install
COPY . .
RUN yarn build

# # production stage
FROM nginx:stable-alpine as production-stage
COPY --from=build-stage /app/dist /usr/share/nginx/html
COPY --from=build-stage /app/nginx/nginx.conf /etc/nginx/nginx.conf

CMD ["nginx", "-g", "daemon off;"]
