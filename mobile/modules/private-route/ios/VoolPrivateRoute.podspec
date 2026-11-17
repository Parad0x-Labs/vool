Pod::Spec.new do |s|
  s.name = 'VoolPrivateRoute'
  s.version = '0.1.0'
  s.summary = 'VOOL away-from-home route to the paired computer through Tor inside the app'
  s.description = s.summary
  s.license = { :type => 'MIT' }
  s.author = 'sls_0x'
  s.homepage = 'https://github.com/Parad0x-Labs/vool'
  s.platform = :ios, '15.1'
  s.source = { :git => 'https://github.com/Parad0x-Labs/vool.git' }
  s.static_framework = true
  s.swift_version = '5.9'
  s.dependency 'ExpoModulesCore'
  # Tor.framework (MIT) with tor (BSD-3-Clause), libevent (BSD-3-Clause) and OpenSSL (Apache-2.0), prebuilt and
  # checked against its published SHA-256 when installed. The no-LZMA build: nothing VOOL needs uses it.
  s.dependency 'Tor/CTor-NoLZMA', '~> 409.15'
  s.source_files = '**/*.swift'
end
