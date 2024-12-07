pipeline {
  agent any
  tools {
    dockerTool 'docker'
  }

  environment {
    BUILD_VERSION = ""
    VERSION = 1
    REGISTRY = 'http://192.168.1.17:5005'
    IMAGE_NAME = 'spotd_sync'
  }

  stages {
    stage('Read Pom Version') {
      steps {
        script {
          BUILD_VERSION = "latest"
          echo "build Version: ${BUILD_VERSION}"
          echo "version Version: ${VERSION}"
        }
      }
    }

    stage('Build Docker Image') {
      steps {
        script {
          // Build Docker image and tag it with the build version
          echo "building to ${IMAGE_NAME} environment... with  version: ${BUILD_VERSION}"
          docker.build("${IMAGE_NAME}:${BUILD_VERSION}", " -f .docker/Dockerfile .")
        }
      }
    }

    stage('Push to Docker Registry') {
      steps {
        script {
          echo "pushing ${IMAGE_NAME} to ${REGISTRY} environment... with  version: ${BUILD_VERSION}"
          docker.withRegistry("${REGISTRY}") {
            docker.image("${IMAGE_NAME}:${BUILD_VERSION}").push()
          }
        }
      }
    }
  }
}