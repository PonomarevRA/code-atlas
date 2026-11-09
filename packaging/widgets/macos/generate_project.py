"""Generate a dependency-free Xcode project for native WidgetKit/AppIntent metadata."""

from pathlib import Path
import plistlib


def generate(root, configurable=False):
    """Describe the host and embedded extension without credentials or global settings."""
    objects = {}

    def add(number, isa, **values):
        """Assign stable object IDs so generated project changes remain reviewable."""
        key = f'{number:024X}'
        objects[key] = {'isa': isa, **values}
        return key

    sources = add(2, 'PBXGroup', children=[], sourceTree='<group>')
    products = add(3, 'PBXGroup', name='Products', children=[], sourceTree='<group>')
    group = add(4, 'PBXGroup', children=[sources, products], sourceTree='<group>')
    host_product = add(
        5, 'PBXFileReference', explicitFileType='wrapper.application',
        path='AG Requests Widgets.app', sourceTree='BUILT_PRODUCTS_DIR',
    )
    extension_product = add(
        6, 'PBXFileReference', explicitFileType='wrapper.app-extension',
        path='AGRequests.appex', sourceTree='BUILT_PRODUCTS_DIR',
    )
    objects[products]['children'] = [host_product, extension_product]

    widget_source = 'RequestsWidget.swift' if configurable else 'StaticWidgets.swift'
    filenames = ['Host.swift', 'Snapshot.swift', 'RequestsView.swift', widget_source]
    source_references = []
    for number, name in enumerate(filenames, 10):
        file_reference = add(
            number, 'PBXFileReference', lastKnownFileType='sourcecode.swift',
            path='Sources/' + name, sourceTree='<group>',
        )
        objects[sources]['children'].append(file_reference)
        source_references.append(add(number + 10, 'PBXBuildFile', fileRef=file_reference))

    phase_settings = {'buildActionMask': 2147483647, 'runOnlyForDeploymentPostprocessing': 0}
    host_sources = add(30, 'PBXSourcesBuildPhase', files=[source_references[0]], **phase_settings)
    extension_sources = add(31, 'PBXSourcesBuildPhase', files=source_references[1:], **phase_settings)
    embedded_file = add(32, 'PBXBuildFile', fileRef=extension_product)
    embedded_phase = add(
        33, 'PBXCopyFilesBuildPhase', name='Embed Widget Extension', dstPath='',
        dstSubfolderSpec=13, files=[embedded_file], **phase_settings,
    )

    configurations = []
    targets = [
        (40, 'Host-Info.plist', 'io.github.ponomarevra.agdesktop.widgets'),
        (41, 'Widget-Info.plist', 'io.github.ponomarevra.agdesktop.widgets.requests'),
    ]
    for number, info, bundle in targets:
        settings = {
            'SDKROOT': 'macosx', 'MACOSX_DEPLOYMENT_TARGET': '14.0',
            'SWIFT_VERSION': '5.0', 'INFOPLIST_FILE': info,
            'PRODUCT_BUNDLE_IDENTIFIER': bundle,
            'PRODUCT_NAME': 'AG Requests Widgets' if number == 40 else 'AGRequests',
            'CODE_SIGN_STYLE': 'Manual', 'CODE_SIGNING_ALLOWED': 'NO',
        }
        if number == 41:
            settings.update(
                APPLICATION_EXTENSION_API_ONLY='YES', SKIP_INSTALL='YES',
                CODE_SIGN_ENTITLEMENTS='Widget.entitlements',
            )
        configuration = add(number, 'XCBuildConfiguration', name='Release', buildSettings=settings)
        configurations.append(add(
            number + 10, 'XCConfigurationList', buildConfigurations=[configuration],
            defaultConfigurationName='Release', defaultConfigurationIsVisible=0,
        ))

    extension = add(
        60, 'PBXNativeTarget', name='AGRequests', productName='AGRequests',
        productReference=extension_product, productType='com.apple.product-type.app-extension',
        buildConfigurationList=configurations[1], buildPhases=[extension_sources], dependencies=[],
    )
    dependency = add(61, 'PBXTargetDependency', target=extension)
    host = add(
        62, 'PBXNativeTarget', name='AGRequestsWidgets', productName='AG Requests Widgets',
        productReference=host_product, productType='com.apple.product-type.application',
        buildConfigurationList=configurations[0], buildPhases=[host_sources, embedded_phase],
        dependencies=[dependency],
    )
    project_configuration = add(70, 'XCBuildConfiguration', name='Release', buildSettings={})
    project_configurations = add(
        71, 'XCConfigurationList', buildConfigurations=[project_configuration],
        defaultConfigurationName='Release', defaultConfigurationIsVisible=0,
    )
    project = add(
        1, 'PBXProject', buildConfigurationList=project_configurations,
        compatibilityVersion='Xcode 14.0', mainGroup=group, productRefGroup=products,
        projectDirPath='', projectRoot='', targets=[host, extension],
        attributes={'LastUpgradeCheck': '1600'},
    )
    folder = root / 'AGRequests.xcodeproj'
    folder.mkdir(exist_ok=True)
    document = {
        'archiveVersion': '1', 'classes': {}, 'objectVersion': '56',
        'objects': objects, 'rootObject': project,
    }
    (folder / 'project.pbxproj').write_bytes(plistlib.dumps(document))
    write_metadata(root)


def write_metadata(root):
    """Keep Xcode-variable executable and bundle identifiers in both target plists."""
    host = {
        'CFBundlePackageType': 'APPL', 'CFBundleExecutable': '$(EXECUTABLE_NAME)',
        'CFBundleIdentifier': '$(PRODUCT_BUNDLE_IDENTIFIER)',
        'CFBundleName': 'AG Requests Widgets', 'CFBundleVersion': '1',
        'CFBundleShortVersionString': '0.1.0', 'LSMinimumSystemVersion': '14.0',
        'NSAppTransportSecurity': {'NSAllowsLocalNetworking': True},
    }
    extension = {
        **host, 'CFBundlePackageType': 'XPC!',
        'NSExtension': {'NSExtensionPointIdentifier': 'com.apple.widgetkit-extension'},
    }
    (root / 'Host-Info.plist').write_bytes(plistlib.dumps(host))
    (root / 'Widget-Info.plist').write_bytes(plistlib.dumps(extension))


if __name__ == '__main__':
    generate(Path(__file__).resolve().parent)
