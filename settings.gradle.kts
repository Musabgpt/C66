pluginManagement { repositories { google(); mavenCentral(); gradlePluginPortal() } }
dependencyResolutionManagement {
    repositoriesMode.set(RepositoriesMode.FAIL_ON_PROJECT_REPOS)
    repositories {
        google()
        maven("https://maven-central.storage-download.googleapis.com/maven2") {
            content { includeGroup("org.xerial") }
        }
        mavenCentral()
    }
}
rootProject.name = "NewAlCloud"
include(":app")
project(":app").projectDir = file("android-lite/app")
