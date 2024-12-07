pipeline {
    agent any
    tools {
        dockerTool 'docker'
    }

    parameters {
        string(name: 'DEPLOY_VERSION', defaultValue: 'latest', description: 'Specify the version to deploy')
    }

    environment {
        SSH_KEY_FILE = credentials('undefined_jenkins_key')
        HOST = 'root@192.168.1.5'
        APP_DIR = '/docker/spotd_sync_music'
        COMPOSE_FILE = ' '
    }


    stages {
        stage('Set Environment Variables') {
            steps {
                script {
                    COMPOSE_FILE = 'compose.yaml'
                }
            }
        }

//         stage('Update Docker Compose') {
//             steps {
//                 script {
//                     echo "updating ${COMPOSE_FILE} image tag ${params.DEPLOY_VERSION}"
//                     sh "sed -i 's|\\VERSION|${params.DEPLOY_VERSION}|' .docker/${COMPOSE_FILE}"
//                 }
//             }
//         }

//         stage('Copy Docker Compose to Remote Server') {
//             steps {
//                 script {
//                         echo "Copying ${COMPOSE_FILE} to Cassandra."
//                         echo  "${SSH_KEY_FILE}"
//                         sh "scp -o StrictHostKeyChecking=no -i ${SSH_KEY_FILE} .docker/${COMPOSE_FILE} ${HOST}:${APP_DIR}"
//                 }
//             }
//         }


        stage('Deploy to Environment') {
            steps {
                script {
                        echo "Running ${COMPOSE_FILE} on Cassandra."
                        sh """
                            ssh -o StrictHostKeyChecking=no -i ${SSH_KEY_FILE} ${HOST} "

                                cd ${APP_DIR}

                                docker pull 192.168.1.17:5005/spotd_sync:latest

                                docker stop spotd_sync_music

                                docker rm spotd_sync_music

                                docker compose -f ${COMPOSE_FILE} up -d
                            "
                        """
                }
            }
        }
    }
}