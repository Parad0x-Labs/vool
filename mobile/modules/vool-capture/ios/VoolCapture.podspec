Pod::Spec.new do |s|
  s.name = 'VoolCapture'
  s.version = '0.1.0'
  s.summary = 'VOOL private camera, Vault and screen rules on iPhone and iPad'
  s.description = s.summary
  s.license = { :type => 'MIT' }
  s.author = 'sls_0x'
  s.homepage = 'https://github.com/Parad0x-Labs/vool'
  s.platform = :ios, '15.1'
  s.source = { :git => 'https://github.com/Parad0x-Labs/vool.git' }
  s.static_framework = true
  s.swift_version = '5.9'
  s.dependency 'ExpoModulesCore'
  s.source_files = '**/*.swift'
end
