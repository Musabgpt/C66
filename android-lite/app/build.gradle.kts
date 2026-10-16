plugins {
    id("com.android.application")
}

dependencies {
    testImplementation("junit:junit:4.13.2")
    testImplementation("org.json:json:20250517")
}

val releaseStoreFile = System.getenv("MUSABAI_RELEASE_STORE_FILE")
val releaseStorePassword = System.getenv("MUSABAI_RELEASE_STORE_PASSWORD")
val releaseKeyAlias = System.getenv("MUSABAI_RELEASE_KEY_ALIAS")
val releaseKeyPassword = System.getenv("MUSABAI_RELEASE_KEY_PASSWORD")
val releaseSigningValues = listOf(
    releaseStoreFile,
    releaseStorePassword,
    releaseKeyAlias,
    releaseKeyPassword,
)
val hasReleaseSigning = releaseSigningValues.all { !it.isNullOrBlank() }
val hasPartialReleaseSigning = releaseSigningValues.any { !it.isNullOrBlank() } && !hasReleaseSigning
val allowDebugSigning = System.getenv("MUSABAI_ALLOW_DEBUG_SIGNING")
    ?.equals("true", ignoreCase = true) == true

if (hasPartialReleaseSigning) {
    throw GradleException(
        "Incomplete MusabAI release signing configuration. " +
            "Provide all MUSABAI_RELEASE_* signing values or none of them."
    )
}

// The native programs (python, llama-server) and the assets (Python's library, NewAl Code) are made by
// native/prepare.sh into src/main/jniLibs and build/generated/assets; see README.md.
android {
    namespace = "dev.newal.code.lite"
    compileSdk = 35
    buildFeatures { buildConfig = true }

    defaultConfig {
        applicationId = "dev.newal.code.lite.connectors"
        minSdk = 26
        targetSdk = 35
        versionCode = (System.getenv("NEWAL_BUILD") ?: "1").toInt()
        versionName = "0.1." + (System.getenv("NEWAL_BUILD") ?: "0")
    }

    sourceSets {
        getByName("main") {
            assets.srcDir(layout.buildDirectory.dir("generated/newal-assets"))
        }
    }

    signingConfigs {
        if (hasReleaseSigning) {
            create("musabRelease") {
                storeFile = file(releaseStoreFile!!)
                storePassword = releaseStorePassword
                keyAlias = releaseKeyAlias
                keyPassword = releaseKeyPassword
                enableV1Signing = true
                enableV2Signing = true
                enableV3Signing = true
                enableV4Signing = false
            }
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            // Production/update builds use only the persistent release identity.
            // Local development may opt into debug signing explicitly; it is never update-compatible
            // with the persistent release identity and CI labels it as development-only.
            signingConfig = when {
                hasReleaseSigning -> signingConfigs.getByName("musabRelease")
                allowDebugSigning -> signingConfigs.getByName("debug")
                else -> null
            }
        }
    }

    packaging {
        // The programs run from the app's native library folder: Android must unpack them at install.
        jniLibs { useLegacyPackaging = true }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
}
