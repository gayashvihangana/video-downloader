/// Text formatting, matching the desktop app (core.human_size / human_time).
library;

/// 999 B, 1 KB, 9.9 MB, 85 MB, 1.5 GB
String humanSize(num? bytes) {
  if (bytes == null || bytes <= 0) return '';
  if (bytes < 1000) return '${bytes.round()} B';

  const units = ['KB', 'MB', 'GB', 'TB'];
  var value = bytes / 1000;
  var unit = 0;
  while (value >= 1000 && unit < units.length - 1) {
    value /= 1000;
    unit++;
  }
  // One decimal below 10 (9.9 MB), none above (85 MB); never "1.0"
  final text = value < 10
      ? ((value * 10).floor() / 10).toStringAsFixed(1)
      : value.floor().toString();
  return '${text.endsWith('.0') ? text.substring(0, text.length - 2) : text} ${units[unit]}';
}

/// 0:05, 1:00, 1:00:00
String humanTime(num? seconds) {
  final total = (seconds ?? 0).round();
  final hours = total ~/ 3600;
  final minutes = (total % 3600) ~/ 60;
  final secs = (total % 60).toString().padLeft(2, '0');
  if (hours > 0) return '$hours:${minutes.toString().padLeft(2, '0')}:$secs';
  return '$minutes:$secs';
}

/// "5:30 PM" today, "Yesterday", or "12 Nov" (like the desktop Downloads page)
String when(DateTime? time, {DateTime? now}) {
  if (time == null) return '';
  now ??= DateTime.now();
  final today = DateTime(now.year, now.month, now.day);
  final day = DateTime(time.year, time.month, time.day);

  if (day == today) {
    final hour = time.hour % 12 == 0 ? 12 : time.hour % 12;
    final minute = time.minute.toString().padLeft(2, '0');
    return '$hour:$minute ${time.hour < 12 ? 'AM' : 'PM'}';
  }
  if (today.difference(day).inDays == 1) return 'Yesterday';

  const months = [
    'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
    'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec',
  ];
  return '${time.day} ${months[time.month - 1]}';
}
