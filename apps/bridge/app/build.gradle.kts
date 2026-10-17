import java.util.Properties

plugins {
    alias(libs.plugins.android.application)
    alias(libs.plugins.kotlin.compose)
    alias(libs.plugins.kotlin.serialization)
}

// Debug builds default to the token in the repo-root .env, so a fresh install connects with no typing.
val env = Properties().apply {
    rootProject.file("../../.env").takeIf { it.exists() }?.reader()?.use(::load)
}

android {
    namespace = "tech.arrakis.archangel.bridge"
    compileSdk = 37

    defaultConfig {
        applicationId = "tech.arrakis.archangel.bridge"
        minSdk = 30
        targetSdk = 36
        versionCode = 1
        versionName = "0.1.0"
        buildConfigField("String", "DEFAULT_URL", "\"\"")
        buildConfigField("String", "DEFAULT_TOKEN", "\"\"")
    }

    buildTypes {
        debug {
            buildConfigField("String", "DEFAULT_URL", "\"ws://localhost:8080/ws/device\"")
            buildConfigField("String", "DEFAULT_TOKEN", "\"${env.getProperty("ARCHANGEL_TOKEN", "")}\"")
        }
    }

    buildFeatures {
        buildConfig = true
        compose = true
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
}

dependencies {
    implementation(platform(libs.compose.bom))
    implementation(libs.compose.material3)
    implementation(libs.activity.compose)
    implementation(libs.okhttp)
    implementation(libs.kotlinx.serialization.json)
    implementation(libs.kotlinx.coroutines.android)

    testImplementation(libs.junit)
    testImplementation(libs.kotlinx.coroutines.test)
}
