package tech.arrakis.archangel.bridge

import android.accessibilityservice.AccessibilityService
import android.accessibilityservice.AccessibilityService.GestureResultCallback
import android.accessibilityservice.AccessibilityService.ScreenshotResult
import android.accessibilityservice.AccessibilityService.TakeScreenshotCallback
import android.accessibilityservice.GestureDescription
import android.content.Intent
import android.graphics.Bitmap
import android.graphics.Path
import android.graphics.Rect
import android.os.Bundle
import android.util.Base64
import android.view.Display
import android.view.accessibility.AccessibilityNodeInfo
import java.io.ByteArrayOutputStream
import kotlin.math.roundToInt
import kotlin.coroutines.resume
import kotlin.coroutines.resumeWithException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlinx.coroutines.withContext

class AndroidDevice(private val service: AccessibilityService) : Device {

    override suspend fun screenshot(scale: Float): Screenshot {
        val result = captureScreen()
        return withContext(Dispatchers.Default) {
            result.hardwareBuffer.use { buffer ->
                val hardware = Bitmap.wrapHardwareBuffer(buffer, result.colorSpace)
                    ?: throw CommandException(ErrorCode.FAILED, "Could not read screenshot")
                val full = hardware.copy(Bitmap.Config.ARGB_8888, false)
                hardware.recycle()
                val bitmap = if (scale == 1f) full else Bitmap.createScaledBitmap(
                    full,
                    (full.width * scale).roundToInt(),
                    (full.height * scale).roundToInt(),
                    true,
                ).also { full.recycle() }
                val jpeg = ByteArrayOutputStream().also { bitmap.compress(Bitmap.CompressFormat.JPEG, JPEG_QUALITY, it) }
                Screenshot(bitmap.width, bitmap.height, Base64.encodeToString(jpeg.toByteArray(), Base64.NO_WRAP))
                    .also { bitmap.recycle() }
            }
        }
    }

    /** Android rate-limits screenshots; absorb that here rather than making every caller retry. */
    private suspend fun captureScreen(): ScreenshotResult {
        var attempt = 1
        while (true) {
            try {
                return takeScreenshot()
            } catch (_: RateLimited) {
                if (attempt++ == SCREENSHOT_ATTEMPTS) {
                    throw CommandException(ErrorCode.FAILED, "Screenshots requested too quickly")
                }
                delay(SCREENSHOT_RETRY_MS)
            }
        }
    }

    private class RateLimited : Exception()

    private suspend fun takeScreenshot(): ScreenshotResult = suspendCancellableCoroutine { continuation ->
        service.takeScreenshot(Display.DEFAULT_DISPLAY, service.mainExecutor, object : TakeScreenshotCallback {
            override fun onSuccess(result: ScreenshotResult) = continuation.resume(result)

            override fun onFailure(errorCode: Int) = continuation.resumeWithException(
                if (errorCode == AccessibilityService.ERROR_TAKE_SCREENSHOT_INTERVAL_TIME_SHORT) RateLimited()
                else CommandException(ErrorCode.FAILED, "Screenshot failed (error $errorCode)"),
            )
        })
    }

    override suspend fun swipe(x1: Float, y1: Float, x2: Float, y2: Float, durationMs: Long) {
        val path = Path().apply {
            moveTo(x1, y1)
            lineTo(x2, y2)
        }
        val stroke = GestureDescription.StrokeDescription(path, 0, durationMs)
        val gesture = GestureDescription.Builder().addStroke(stroke).build()
        val completed = suspendCancellableCoroutine { continuation ->
            val callback = object : GestureResultCallback() {
                override fun onCompleted(description: GestureDescription) = continuation.resume(true)
                override fun onCancelled(description: GestureDescription) = continuation.resume(false)
            }
            if (!service.dispatchGesture(gesture, callback, null)) continuation.resume(false)
        }
        if (!completed) throw CommandException(ErrorCode.FAILED, "Gesture was cancelled")
    }

    override fun type(text: String) {
        val field = service.findFocus(AccessibilityNodeInfo.FOCUS_INPUT)
            ?: throw CommandException(ErrorCode.NOT_FOUND, "No focused input field")
        val arguments = Bundle().apply {
            putCharSequence(AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE, text)
        }
        if (!field.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT, arguments)) {
            throw CommandException(ErrorCode.FAILED, "Focused field rejected the text")
        }
    }

    override fun key(key: GlobalKey) {
        val action = when (key) {
            GlobalKey.Back -> AccessibilityService.GLOBAL_ACTION_BACK
            GlobalKey.Home -> AccessibilityService.GLOBAL_ACTION_HOME
            GlobalKey.Recents -> AccessibilityService.GLOBAL_ACTION_RECENTS
            GlobalKey.Notifications -> AccessibilityService.GLOBAL_ACTION_NOTIFICATIONS
            GlobalKey.QuickSettings -> AccessibilityService.GLOBAL_ACTION_QUICK_SETTINGS
            GlobalKey.Lock -> AccessibilityService.GLOBAL_ACTION_LOCK_SCREEN
        }
        if (!service.performGlobalAction(action)) throw CommandException(ErrorCode.FAILED, "System refused $key")
    }

    override fun launch(packageName: String) {
        val intent = service.packageManager.getLaunchIntentForPackage(packageName)
            ?: throw CommandException(ErrorCode.NOT_FOUND, "No launchable app: $packageName")
        service.startActivity(intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
    }

    override fun apps(): Apps {
        val packageManager = service.packageManager
        val launcher = Intent(Intent.ACTION_MAIN).addCategory(Intent.CATEGORY_LAUNCHER)
        val apps = packageManager.queryIntentActivities(launcher, 0)
            .map { App(it.loadLabel(packageManager).toString(), it.activityInfo.packageName) }
            .distinctBy { it.packageName }
            .sortedBy { it.label.lowercase() }
        return Apps(apps)
    }

    override fun uiTree() = UiTree(service.windows.mapNotNull { it.root?.toNode() })

    private fun AccessibilityNodeInfo.toNode(): Node {
        val bounds = Rect().also(::getBoundsInScreen)
        val flags = buildList {
            if (isClickable) add("clickable")
            if (isEditable) add("editable")
            if (isScrollable) add("scrollable")
            if (isFocused) add("focused")
        }
        val children = (0 until childCount)
            .mapNotNull(::getChild)
            .filter { it.isVisibleToUser }
            .map { it.toNode() }
        return Node(
            className = className?.toString()?.substringAfterLast('.').orEmpty(),
            text = text?.toString()?.ifEmpty { null },
            desc = contentDescription?.toString()?.ifEmpty { null },
            id = viewIdResourceName,
            bounds = listOf(bounds.left, bounds.top, bounds.right, bounds.bottom),
            flags = flags.ifEmpty { null },
            children = children.ifEmpty { null },
        )
    }

    private companion object {
        const val JPEG_QUALITY = 80
        const val SCREENSHOT_ATTEMPTS = 3
        const val SCREENSHOT_RETRY_MS = 400L
    }
}
