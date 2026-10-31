import 'package:flutter_test/flutter_test.dart';
import 'package:flutter/material.dart';
import 'package:newmean_pii_example/main.dart';

void main() {
  testWidgets('shows the input and the redact button', (tester) async {
    await tester.pumpWidget(const MaterialApp(home: RedactPage()));
    expect(find.text('Redact'), findsOneWidget);
    expect(find.text('nano'), findsOneWidget);
  });
}
